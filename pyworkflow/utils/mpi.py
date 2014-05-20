# **************************************************************************
# *
# * Authors:     J.M. De la Rosa Trevin (jmdelarosa@cnb.csic.es)
# *
# * Unidad de  Bioinformatica of Centro Nacional de Biotecnologia , CSIC
# *
# * This program is free software; you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation; either version 2 of the License, or
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
# *  e-mail address 'jmdelarosa@cnb.csic.es'
# *
# **************************************************************************
"""
This module contains some MPI utilities
"""

from time import sleep
from process import buildRunCommand, runCommand


TAG_RUN_JOB = 1000

def runJobMPI(log, programname, params, mpiComm, mpiDest,
              numberOfMpi=1, numberOfThreads=1,
              runInBackground=False, hostConfig=None):
    """ Send the command to the MPI node in which it will be executed. """
    print "runJobMPI: hostConfig: ", hostConfig
    command = buildRunCommand(log, programname, params,
                              numberOfMpi, numberOfThreads, 
                              runInBackground, hostConfig)
    print "Sending command: %s to %d" % (command, mpiDest)
    mpiComm.send(command, dest=mpiDest, tag=TAG_RUN_JOB + mpiDest)
    request = mpiComm.irecv(dest=mpiDest, tag=TAG_RUN_JOB + mpiDest)
    while True:
        done, result = request.test()
        if done:
            break
        sleep(1)

    if isinstance(result, str):
        raise Exception(result)
    
    # If not string should be the retcode
    return result
    

def runJobMPISlave(mpiComm):
    """ This slave will be receiving commands to execute
    until 'None' is received. 
    """
    rank = mpiComm.Get_rank()
    print "Running runJobMPISlave: ", rank
    while True:
        request = mpiComm.irecv(dest=0, tag=TAG_RUN_JOB + rank)
        while True:
            done, command = request.test()
            if done:
                break
            sleep(1)
        print "Slave %d, received command: %s" % (rank, command)
        if command == 'None':
            break
        try:
            result = runCommand(command)
        except Exception, e:
            result = str(e)
        mpiComm.send(result, dest=0, tag=TAG_RUN_JOB + rank)
    print "finishing slave...", rank
    

