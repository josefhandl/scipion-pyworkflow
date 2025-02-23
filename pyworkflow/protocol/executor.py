# **************************************************************************
# *
# * Authors:     J.M. De la Rosa Trevin (jmdelarosa@cnb.csic.es)
# *
# * Unidad de  Bioinformatica of Centro Nacional de Biotecnologia, CSIC
# *
# * This program is free software; you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation; either version 3 of the License, or
# * (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA
# * 02111-1307  USA
# *
# *  All comments concerning this program package may be sent to the
# *  e-mail address 'scipion@cnb.csic.es'
# *
# **************************************************************************
"""
This module have the classes for execution of protocol steps.
The basic one will run steps, one by one, after completion.
There is one based on threads to execute steps in parallel
using different threads and the last one with MPI processes.
"""

import logging
logger = logging.getLogger(__name__)
import time
import datetime
import threading
import os
import re
from subprocess import Popen, PIPE

import pyworkflow.utils.process as process
from pyworkflow.utils.path import getParentFolder, removeExt
from . import constants as cts

from .launch import _submit, UNKNOWN_JOBID


class StepExecutor:
    """ Run a list of Protocol steps. """
    def __init__(self, hostConfig, **kwargs):
        self.hostConfig = hostConfig
        self.gpuList = kwargs.get(cts.GPU_LIST, None)

    def getGpuList(self):
        """ Return the GPU list assigned to current thread. """
        return self.gpuList

    def runJob(self, log, programName, params,           
               numberOfMpi=1, numberOfThreads=1,
               env=None, cwd=None):
        """ This function is a wrapper around runJob, 
        providing the host configuration. 
        """
        process.runJob(log, programName, params,
                       numberOfMpi, numberOfThreads, 
                       self.hostConfig,
                       env=env, cwd=cwd, gpuList=self.getGpuList())
        
    def _getRunnable(self, steps, n=1):
        """ Return the n steps that are 'new' and all its
        dependencies have been finished, or None if none ready.
        """
        rs = []  # return a list of runnable steps

        for s in steps:
            if (s.getStatus() == cts.STATUS_NEW and
                    all(steps[i-1].isFinished() for i in s._prerequisites)):
                rs.append(s)
                if len(rs) == n:
                    break
        return rs
    
    def _arePending(self, steps):
        """ Return True if there are pending steps (either running or waiting)
        that can be done and thus enable other steps to be executed.
        """
        return any(s.isRunning() or s.isWaiting() for s in steps)
    
    def runSteps(self, steps, 
                 stepStartedCallback, 
                 stepFinishedCallback,
                 stepsCheckCallback,
                 stepsCheckSecs=3):
        # Even if this will run the steps in a single thread
        # let's follow a similar approach than the parallel one
        # In this way we can take into account the steps graph
        # dependency and also the case when using streaming

        delta = datetime.timedelta(seconds=stepsCheckSecs)
        lastCheck = datetime.datetime.now()

        while True:
            # Get a step to run, if there is any
            runnableSteps = self._getRunnable(steps)

            if runnableSteps:
                step = runnableSteps[0]
                # We found a step to work in, so let's start a new
                # thread to do the job and book it.
                step.setRunning()
                stepStartedCallback(step)
                step.run()
                doContinue = stepFinishedCallback(step)
            
                if not doContinue:
                    break

            elif self._arePending(steps):
                # We have not found any runnable step, but still there
                # there are some running or waiting for dependencies
                # So, let's wait a bit to check if something changes
                time.sleep(0.5)
            else:
                # No steps to run, neither running or waiting
                # So, we are done, either failed or finished :)
                break

            now = datetime.datetime.now()
            if now - lastCheck > delta:
                stepsCheckCallback()
                lastCheck = now

        stepsCheckCallback()  # one last check to finalize stuff


class StepThread(threading.Thread):
    """ Thread to run Steps in parallel. """
    def __init__(self, thId, step, lock):
        threading.Thread.__init__(self)
        self.thId = thId
        self.step = step
        self.lock = lock

    def run(self):
        error = None
        try:
            self.step._run()  # not self.step.run() , to avoid race conditions
        except Exception as e:
            error = str(e)
            logger.error("Couldn't run the code in a thread." , exc_info=e)
        finally:
            with self.lock:
                if error is None:
                    self.step.setFinished()
                else:
                    self.step.setFailed(error)



class ThreadStepExecutor(StepExecutor):
    """ Run steps in parallel using threads. """
    def __init__(self, hostConfig, nThreads, **kwargs):
        StepExecutor.__init__(self, hostConfig, **kwargs)
        self.numberOfProcs = nThreads
        # If the gpuList was specified, we need to distribute GPUs among
        # all the threads
        self.gpuDict = {}

        if self.gpuList:
            nodes = range(nThreads)
            nGpu = len(self.gpuList)

            if nGpu > nThreads:
                chunk = int(nGpu / nThreads)
                for i, node in enumerate(nodes):
                    self.gpuDict[node] = list(self.gpuList[i*chunk:(i+1)*chunk])
            else:
                # Expand gpuList repeating until reach nThreads items
                if nThreads > nGpu:
                    newList = self.gpuList * (int(nThreads/nGpu)+1)
                    self.gpuList = newList[:nThreads]

                for node, gpu in zip(nodes, self.gpuList):
                    self.gpuDict[node] = [gpu]

    def getGpuList(self):
        """ Return the GPU list assigned to current thread
        or empty list if not using GPUs. """
        return self.gpuDict.get(threading.currentThread().thId, [])
        
    def runSteps(self, steps, 
                 stepStartedCallback, 
                 stepFinishedCallback,
                 stepsCheckCallback,
                 stepsCheckSecs=5):
        """
        Creates threads and synchronize the steps execution.

        :param steps: list of steps to run
        :param stepStartedCallback: callback to be called before starting any step
        :param stepFinishedCallback: callback to be run after all steps are done
        :param stepsCheckCallback: callback to check if there are new steps to add (streaming)
        :param stepsCheckSecs: seconds between stepsCheckCallback calls

        """

        delta = datetime.timedelta(seconds=stepsCheckSecs)
        lastCheck = datetime.datetime.now()

        sharedLock = threading.Lock()

        runningSteps = {}  # currently running step in each node ({node: step})
        freeNodes = list(range(self.numberOfProcs))  # available nodes to send jobs

        while True:
            # See which of the runningSteps are not really running anymore.
            # Update them and freeNodes, and call final callback for step.
            with sharedLock:
                nodesFinished = [node for node, step in runningSteps.items()
                                 if not step.isRunning()]
            doContinue = True
            for node in nodesFinished:
                step = runningSteps.pop(node)  # remove entry from runningSteps
                freeNodes.append(node)  # the node is available now
                # Notify steps termination and check if we should continue
                doContinue = stepFinishedCallback(step)
                if not doContinue:
                    break

            if not doContinue:
                break

            anyLaunched = False
            # If there are available nodes, send next runnable step.
            with sharedLock:
                if freeNodes:
                    runnableSteps = self._getRunnable(steps, len(freeNodes))

                    for step in runnableSteps:
                        # We found a step to work in, so let's start a new
                        # thread to do the job and book it.
                        anyLaunched = True
                        step.setRunning()
                        stepStartedCallback(step)
                        node = freeNodes.pop()  # take an available node
                        runningSteps[node] = step
                        t = StepThread(node, step, sharedLock)
                        # won't keep process up if main thread ends
                        t.daemon = True
                        t.start()
                anyPending = self._arePending(steps)

            if not anyLaunched:
                if anyPending:  # nothing running
                    time.sleep(0.5)
                else:
                    break  # yeah, we are done, either failed or finished :)

            now = datetime.datetime.now()
            if now - lastCheck > delta:
                stepsCheckCallback()
                lastCheck = now

        stepsCheckCallback()

        # Wait for all threads now.
        for t in threading.enumerate():
            if t is not threading.current_thread():
                t.join()


class QueueStepExecutor(ThreadStepExecutor):
    def __init__(self, hostConfig, submitDict, nThreads, **kwargs):
        ThreadStepExecutor.__init__(self, hostConfig, nThreads, **kwargs)
        self.submitDict = submitDict
        # Command counter per thread
        self.threadCommands = {}
        for threadId in range(nThreads):
            self.threadCommands[threadId] = 0

        if nThreads > 1:
            self.runJobs = ThreadStepExecutor.runSteps
        else:
            self.runJobs = StepExecutor.runSteps

    def runJob(self, log, programName, params, numberOfMpi=1, numberOfThreads=1, env=None, cwd=None):
        threadId = threading.current_thread().thId
        submitDict = dict(self.hostConfig.getQueuesDefault())
        submitDict.update(self.submitDict)
        submitDict['JOB_COMMAND'] = process.buildRunCommand(programName, params, numberOfMpi,
                                                            self.hostConfig, env,
                                                            gpuList=self.getGpuList())
        self.threadCommands[threadId] += 1
        subthreadId = '-%s-%s' % (threadId, self.threadCommands[threadId])
        submitDict['JOB_NAME'] = submitDict['JOB_NAME'] + subthreadId
        submitDict['JOB_SCRIPT'] = os.path.abspath(removeExt(submitDict['JOB_SCRIPT']) + subthreadId + ".job")
        submitDict['JOB_LOGS'] = os.path.join(getParentFolder(submitDict['JOB_SCRIPT']), submitDict['JOB_NAME'])

        jobid = _submit(self.hostConfig, submitDict, cwd, env)

        if (jobid is None) or (jobid == UNKNOWN_JOBID):
            logger.info("jobId is none therefore we set it to fail")
            raise Exception("Failed to submit to queue.")

        status = cts.STATUS_RUNNING
        wait = 3

        # Check status while job running
        # REVIEW this to minimize the overhead in time put by this delay check
        while self._checkJobStatus(self.hostConfig, jobid) == cts.STATUS_RUNNING:
            time.sleep(wait)
            if wait < 300:
                wait += 3

        return status

    def _checkJobStatus(self, hostConfig, jobid):

        command = hostConfig.getCheckCommand() % {"JOB_ID": jobid}
        p = Popen(command, shell=True, stdout=PIPE, preexec_fn=os.setsid)

        out = p.communicate()[0].decode(errors='backslashreplace')

        jobDoneRegex = hostConfig.getJobDoneRegex()

        # If nothing is returned we assume job is no longer in queue and thus finished
        if out == "":
            return cts.STATUS_FINISHED
        # If some string is returned we use the JOB_DONE_REGEX variable (if present) to infer the status
        elif jobDoneRegex is not None:
            s = re.search(jobDoneRegex, out)
            if s:
                return cts.STATUS_FINISHED
            else:
                return cts.STATUS_RUNNING
        # If JOB_DONE_REGEX is not defined and queue has returned something we assume that job is still running
        else:
            return cts.STATUS_RUNNING


class MPIStepExecutor(ThreadStepExecutor):
    """ Run steps in parallel using threads.
    But call runJob through MPI workers.
    """
    def __init__(self, hostConfig, nMPI, comm, **kwargs):
        ThreadStepExecutor.__init__(self, hostConfig, nMPI, **kwargs)
        self.comm = comm
    
    def runJob(self, log, programName, params,
               numberOfMpi=1, numberOfThreads=1, env=None, cwd=None):
        # Import mpi here so if MPI4py was not properly compiled
        # we can still run in parallel with threads.
        from pyworkflow.utils.mpi import runJobMPI
        node = threading.current_thread().thId + 1
        runJobMPI(programName, params, self.comm, node,
                  numberOfMpi, hostConfig=self.hostConfig, env=env, cwd=cwd,
                  gpuList=self.getGpuList())

    def runSteps(self, steps, 
                 stepStartedCallback, 
                 stepFinishedCallback,
                 checkStepsCallback,
                 stepsCheckSecs=5):
        """
        Creates mpiprocesses using numpy and synchronize the steps execution.

        :param steps: list of steps to run
        :param stepStartedCallback: callback to be called before starting any step
        :param stepFinishedCallback: callback to be run after all steps are done
        :param stepsCheckCallback: callback to check if there are new steps to add (streaming)
        :param stepsCheckSecs: seconds between stepsCheckCallback calls

        """


        ThreadStepExecutor.runSteps(self, steps,
                                    stepStartedCallback, 
                                    stepFinishedCallback,
                                    checkStepsCallback,
                                    stepsCheckSecs=stepsCheckSecs)

        # Import mpi here so if MPI4py was not properly compiled
        # we can still run in parallel with threads.
        from pyworkflow.utils.mpi import TAG_RUN_JOB

        # Send special command 'None' to MPI slaves to notify them
        # that there are no more jobs to do and they can finish.
        for node in range(1, self.numberOfProcs+1):
            self.comm.send('None', dest=node, tag=(TAG_RUN_JOB+node))
