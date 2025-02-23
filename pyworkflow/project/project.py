#!/usr/bin/env python
# **************************************************************************
# *
# * Authors:     J.M. De la Rosa Trevin (delarosatrevin@scilifelab.se) [1]
# *
# * [1] SciLifeLab, Stockholm University
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
import logging
logger = logging.getLogger(__name__)

import datetime as dt
import json
import os
import re
import time
import traceback
from collections import OrderedDict

import pyworkflow as pw
from pyworkflow.constants import PROJECT_DBNAME, PROJECT_SETTINGS
import pyworkflow.object as pwobj
import pyworkflow.protocol as pwprot
import pyworkflow.utils as pwutils
from pyworkflow.mapper import SqliteMapper
from pyworkflow.protocol.constants import (MODE_RESTART, MODE_RESUME,
                                           STATUS_INTERACTIVE, ACTIVE_STATUS,
                                           UNKNOWN_JOBID, INITIAL_SLEEP_TIME)
from pyworkflow.protocol.protocol import ProtImportBase

from . import config


OBJECT_PARENT_ID = pwobj.OBJECT_PARENT_ID
PROJECT_LOGS = 'Logs'
PROJECT_RUNS = 'Runs'
PROJECT_TMP = 'Tmp'
PROJECT_UPLOAD = 'Uploads'
PROJECT_CONFIG = '.config'
PROJECT_CREATION_TIME = 'CreationTime'

# Regex to get numbering suffix and automatically propose runName
REGEX_NUMBER_ENDING = re.compile('(?P<prefix>.+)(?P<number>\(\d*\))\s*$')
REGEX_NUMBER_ENDING_CP = re.compile('(?P<prefix>.+\s\(copy)(?P<number>.*)\)\s*$')


class Project(object):
    """This class will handle all information 
    related with a Project"""

    @classmethod
    def getDbName(cls):
        """ Return the name of the database file of projects. """
        return PROJECT_DBNAME

    def __init__(self, domain, path):
        """
        Create a new Project instance.
        :param domain: The application domain from where to get objects and
            protocols.
        :param path: Path where the project will be created/loaded
        """
        self._domain = domain
        self.name = path
        self.shortName = os.path.basename(path)
        self.path = os.path.abspath(path)
        self._isLink = os.path.islink(path)
        self._isInReadOnlyFolder = False
        self.pathList = []  # Store all related paths
        self.dbPath = self.__addPath(PROJECT_DBNAME)
        self.logsPath = self.__addPath(PROJECT_LOGS)
        self.runsPath = self.__addPath(PROJECT_RUNS)
        self.tmpPath = self.__addPath(PROJECT_TMP)
        self.uploadPath = self.__addPath(PROJECT_UPLOAD)
        self.settingsPath = self.__addPath(PROJECT_SETTINGS)
        self.configPath = self.__addPath(PROJECT_CONFIG)
        self.runs = None
        self._runsGraph = None
        self._transformGraph = None
        self._sourceGraph = None
        self.address = ''
        self.port = pwutils.getFreePort()
        self.mapper = None
        self.settings = None
        # Host configuration
        self._hosts = None
        #  Creation time should be stored in project.sqlite when the project
        # is created and then loaded with other properties from the database
        self._creationTime = None
        # Time stamp with the last run has been updated
        self._lastRunTime = None

    def getObjId(self):
        """ Return the unique id assigned to this project. """
        return os.path.basename(self.path)

    def __addPath(self, *paths):
        """Store a path needed for the project"""
        p = self.getPath(*paths)
        self.pathList.append(p)
        return p

    def getPath(self, *paths):
        """Return path from the project root"""
        if paths:
            return os.path.join(*paths)
        else:
            return self.path

    def isLink(self):
        """Returns if the project path is a link to another folder."""
        return self._isLink

    def getDbPath(self):
        """ Return the path to the sqlite db. """
        return self.dbPath

    def getDbLastModificationDate(self):
        """ Return the last modification date of the database """
        pwutils.getFileLastModificationDate(self.getDbPath())

    def getCreationTime(self):
        """ Return the time when the project was created. """
        # In project.create method, the first object inserted
        # in the mapper should be the creation time
        return self._creationTime

    def getSettingsCreationTime(self):
        return self.settings.getCreationTime()

    def getElapsedTime(self):
        """ Returns the time elapsed from the creation to the last
        execution time. """
        if self._creationTime and self._lastRunTime:
            creationTs = self._creationTime
            lastRunTs = self._lastRunTime.datetime()
            return lastRunTs - creationTs
        return None

    def getLeftTime(self):
        lifeTime = self.settings.getLifeTime()
        if lifeTime:
            td = dt.timedelta(hours=lifeTime)
            return td - self.getElapsedTime()
        else:
            return None

    def setDbPath(self, dbPath):
        """ Set the project db path.
        This function is used when running a protocol where
        a project is loaded but using the protocol own sqlite file.
        """
        # First remove from pathList the old dbPath
        self.pathList.remove(self.dbPath)
        self.dbPath = os.path.abspath(dbPath)
        self.pathList.append(self.dbPath)

    def getName(self):
        return self.name

    def getDomain(self):
        return self._domain

    # TODO: maybe it has more sense to use this behaviour
    # for just getName function...
    def getShortName(self):
        return self.shortName

    def getTmpPath(self, *paths):
        return self.getPath(PROJECT_TMP, *paths)

    def getLogPath(self, *paths):
        return self.getPath(PROJECT_LOGS, *paths)

    def getSettings(self):
        return self.settings

    def saveSettings(self):
        # Read only mode
        if not self.openedAsReadOnly():
            self.settings.write()

    def createSettings(self, runsView=1, readOnly=False):
        self.settings = config.ProjectSettings()
        self.settings.setRunsView(runsView)
        self.settings.setReadOnly(readOnly)
        self.settings.write(self.settingsPath)
        return self.settings

    def createMapper(self, sqliteFn):
        """ Create a new SqliteMapper object and pass as classes dict
        all globals and update with data and protocols from em.
        """
        classesDict = pwobj.Dict(default=pwprot.LegacyProtocol)
        classesDict.update(self._domain.getMapperDict())
        classesDict.update(config.__dict__)
        return SqliteMapper(sqliteFn, classesDict)

    def load(self, dbPath=None, hostsConf=None, protocolsConf=None, chdir=True,
             loadAllConfig=True):
        """
        Load project data, configuration and settings.

        :param dbPath: the path to the project database.
            If None, use the project.sqlite in the project folder.
        :param hostsConf: where to read the host configuration.
            If None, check if exists in .config/hosts.conf
            or read from ~/.config/scipion/hosts.conf
        :param protocolsConf: Not used
        :param chdir: If True, os.cwd will be set to project's path.
        :param loadAllConfig: If True, settings from settings.sqlite will also be loaded

        """

        if not os.path.exists(self.path):
            raise Exception("Cannot load project, path doesn't exist: %s"
                            % self.path)

        # If folder is read only, flag it and warn about it.
        if not os.access(self.path, os.W_OK):
            self._isInReadOnlyFolder = True
            logger.warning("Project \"%s\": you don't have write permissions "
                  "for project folder. Loading asd READ-ONLY." % self.shortName)

        if chdir:
            os.chdir(self.path)  # Before doing nothing go to project dir

        try:
            self._loadDb(dbPath)
            self._loadHosts(hostsConf)

            if loadAllConfig:

                # FIXME: Handle settings argument here

                # It is possible that settings does not exists if
                # we are loading a project after a Project.setDbName,
                # used when running protocols
                settingsPath = os.path.join(self.path, self.settingsPath)

                logger.debug("settingsPath: %s" % settingsPath)

                if os.path.exists(settingsPath):
                    self.settings = config.ProjectSettings.load(settingsPath)
                else:
                    logger.info("settings is None")
                    self.settings = None

            self._loadCreationTime()

        # Catch DB not found exception (when loading a project from a folder
        #  without project.sqlite
        except MissingProjectDbException as noDBe:
            # Raise it at before: This is a critical error and should be raised
            raise noDBe

        # Catch any less severe exception..to allow at least open the project.
        # except Exception as e:
        #     logger.info("ERROR: Project %s load failed.\n"
        #           "       Message: %s\n" % (self.path, e))

    def _loadCreationTime(self):
        # Load creation time, it should be in project.sqlite or
        # in some old projects it is found in settings.sqlite

        creationTime = self.mapper.selectBy(name=PROJECT_CREATION_TIME)

        if creationTime:  # CreationTime was found in project.sqlite
            self._creationTime = creationTime[0].datetime()
        else:
            # We should read the creation time from settings.sqlite and
            # update the CreationTime in the project.sqlite
            self._creationTime = self.getSettingsCreationTime()
            self._storeCreationTime(self._creationTime)

    # ---- Helper functions to load different pieces of a project
    def _loadDb(self, dbPath):
        """ Load the mapper from the sqlite file in dbPath. """
        if dbPath is not None:
            self.setDbPath(dbPath)

        absDbPath = os.path.join(self.path, self.dbPath)
        if not os.path.exists(absDbPath):
            raise MissingProjectDbException(
                "Project database not found at '%s'" % absDbPath)
        self.mapper = self.createMapper(absDbPath)

    def closeMapper(self):
        if self.mapper is not None:
            self.mapper.close()
            self.mapper = None

    def getLocalConfigHosts(self):
        """ Return the local file where the project will try to
        read the hosts configuration. """
        return self.getPath(PROJECT_CONFIG, pw.Config.SCIPION_HOSTS)

    def _loadHosts(self, hosts):
        """ Loads hosts configuration from hosts file. """
        # If the host file is not passed as argument...
        configHosts = pw.Config.SCIPION_HOSTS
        projHosts = self.getLocalConfigHosts()

        if hosts is None:
            # Try first to read it from the project file .config./hosts.conf
            if os.path.exists(projHosts):
                hostsFile = projHosts
            else:
                localDir = os.path.dirname(pw.Config.SCIPION_LOCAL_CONFIG)
                hostsFile = os.path.join(localDir, configHosts)
        else:
            pwutils.copyFile(hosts, projHosts)
            hostsFile = hosts

        self._hosts = pwprot.HostConfig.load(hostsFile)

    def getHostNames(self):
        """ Return the list of host name in the project. """
        return list(self._hosts.keys())

    def getHostConfig(self, hostName):
        if hostName in self._hosts:
            hostKey = hostName
        else:
            hostKey = self.getHostNames()[0]
            logger.warning("Protocol host '%s' not found." % hostName)
            logger.warning("         Using '%s' instead." % hostKey)

        return self._hosts[hostKey]

    def getProtocolView(self):
        """ Returns de view selected in the tree when it was persisted"""
        return self.settings.getProtocolView()

    def create(self, runsView=1, readOnly=False, hostsConf=None,
               protocolsConf=None):
        """Prepare all required paths and files to create a new project.

        :param runsView: default view to associate the project with
        :param readOnly: If True, project will be loaded as read only.
        :param hostsConf: Path to the host.conf to be used when executing protocols
        :param protocolsConf: Not used.
        """
        # Create project path if not exists
        pwutils.path.makePath(self.path)
        os.chdir(self.path)  # Before doing nothing go to project dir
        self._cleanData()
        logger.info("Creating project at %s" % os.path.abspath(self.dbPath))
        # Create db through the mapper
        self.mapper = self.createMapper(self.dbPath)
        # Store creation time
        self._storeCreationTime(dt.datetime.now())
        # Load settings from .conf files and write .sqlite
        self.settings = self.createSettings(runsView=runsView,
                                            readOnly=readOnly)
        # Create other paths inside project
        for p in self.pathList:
            pwutils.path.makePath(p)

        self._loadHosts(hostsConf)

    def _storeCreationTime(self, creationTime):
        """ Store the creation time in the project db. """
        # Store creation time
        creation = pwobj.String(objName=PROJECT_CREATION_TIME)
        creation.set(creationTime)
        self.mapper.insert(creation)
        self.mapper.commit()

    def _cleanData(self):
        """Clean all project data"""
        pwutils.path.cleanPath(*self.pathList)

    def _continueWorkflow(self, continuedProtList=None, errorsList=None):
        """
        This function continue a workflow from a selected protocol.
        The previous results are preserved.
        Actions done here are:
        1. if the protocol list exists (for each protocol)
            1.1  if the protocol is not an interactive protocol
            1.1.1. If the protocol is in streaming (CONTINUE ACTION):
                       - 'dataStreaming' parameter if the protocol is an import
                          protocol
                       -  check if the __stepsCheck function exist and it's not
                          the same implementation of the base class
                          (worksInStreaming function)
                        1.1.1.1 Open the protocol sets, store and save them in
                                the  database
                       1.1.1.2 Change the protocol status (SAVED)
                       1.1.1.3 Schedule the protocol
                   Else Restart the workflow from that point (RESTART ACTION) if
                   at least one protocol in streaming has been launched
        """
        if continuedProtList is not None:
            for protocol, level in continuedProtList.values():
                if not protocol.isInteractive():
                    if protocol.worksInStreaming():
                        attrSet = [attr for name, attr in
                                   protocol.iterOutputAttributes(pwprot.Set)]
                        try:
                            if attrSet:
                                for attr in attrSet:
                                    attr.setStreamState(attr.STREAM_OPEN)
                                    attr.write()
                                    attr.close()
                            protocol.setStatus(pwprot.STATUS_SAVED)
                            protocol._updateSteps(lambda step: step.setStatus(pwprot.STATUS_SAVED))
                            protocol.setMapper(self.createMapper(protocol.getDbPath()))
                            protocol._store()
                            self._storeProtocol(protocol)
                            self.scheduleProtocol(protocol,
                                                  initialSleepTime=level*INITIAL_SLEEP_TIME)
                        except Exception as ex:
                            errorsList.append("Error trying to launch the "
                                              "protocol: %s\nERROR: %s\n" %
                                              (protocol.getObjLabel(), ex))
                            break
                    else:
                        if level != 0:
                            # we make sure that at least one protocol in streaming
                            # has been launched
                            self._restartWorkflow({protocol.getObjId(): (protocol, level)},
                                                  errorsList)

                        else:
                            errorsList.append(("Error trying to launch the "
                                               "protocol: %s\nERROR: The protocol is "
                                               "not in streaming" %
                                               (protocol.getObjLabel())))
                            break

    def _restartWorkflow(self, restartedProtList=None, errorsList=None):
        """
        This function restart a workflow from a selected protocol.
        All previous results will be deleted
        Actions done here are:
        1. Set the protocol run mode (RESTART). All previous results will be
           deleted
        2. Schedule the protocol if not is an interactive protocol
        3. For each of the dependents protocols, repeat from step 1
        """
        if restartedProtList is not None:
            for protocol, level in restartedProtList.values():
                if not protocol.isInteractive():
                    try:
                        protocol.runMode.set(MODE_RESTART)
                        self.scheduleProtocol(protocol,
                                              initialSleepTime=level*INITIAL_SLEEP_TIME)
                    except Exception as ex:
                        errorsList.append("Error trying to restart a protocol: %s"
                                          "\nERROR: %s\n" % (protocol.getObjLabel(),
                                                             ex))
                        break
                else:
                    protocol.setStatus(pwprot.STATUS_SAVED)
                    self._storeProtocol(protocol)
                    protocol.runMode.set(MODE_RESTART)
                    self._setupProtocol(protocol)
                    protocol.makePathsAndClean()  # Create working dir if necessary
                    # Delete the relations created by this protocol
                    self.mapper.deleteRelations(self)
                    self.mapper.commit()
                    self.mapper.store(protocol)
                    self.mapper.commit()

    def _fixProtParamsConfiguration(self, protocol=None):
        """
        This function fix:
        1. The old parameters configuration in the protocols.
           Now, dependent protocols have a pointer to the parent protocol, and
           the extended parameter has a parent output value
        """
        # Take the old configuration attributes and fix the pointer
        oldStylePointerList = [item for key, item in
                               protocol.iterInputAttributes()
                               if not isinstance(item.getObjValue(),
                                                 pwprot.Protocol)]
        if oldStylePointerList:
            # Fix the protocol parameters
            for pointer in oldStylePointerList:
                auxPointer = pointer.getObjValue()
                pointer.set(self.getRunsGraph().getNode(str(pointer.get().getObjParentId())).run)
                pointer.setExtended(auxPointer.getLastName())
                protocol._store()
                self._storeProtocol(protocol)
                self._updateProtocol(protocol)
                self.mapper.commit()

    def stopWorkFlow(self, activeProtList):
        """
        This function can stop a workflow from a selected protocol
        :param initialProtocol: selected protocol
        """
        errorProtList = []
        for protocol in activeProtList:
            try:
                self.stopProtocol(protocol)
            except Exception:
                errorProtList.append(protocol)
        return errorProtList

    def resetWorkFlow(self, workflowProtocolList):
        """
        This function can reset a workflow from a selected protocol
        :param initialProtocol: selected protocol
        """
        errorProtList = []
        if workflowProtocolList:
            for protocol, level in workflowProtocolList.values():
                if protocol.getStatus() != pwprot.STATUS_SAVED:
                    try:
                        self.resetProtocol(protocol)
                    except Exception:
                        errorProtList.append(protocol)
        return errorProtList

    def launchWorkflow(self, workflowProtocolList, mode=MODE_RESUME):
        """
        This function can launch a workflow from a selected protocol in two
        modes depending on the 'mode' value (RESTART, CONTINUE)
        Actions done here are:

        1. Check if the workflow has active protocols.
        2. Fix the workflow if is not properly configured
        3. Restart or Continue a workflow starting from the protocol depending
            on the 'mode' value

        """
        errorsList = []
        if mode == MODE_RESTART:
            self._restartWorkflow(workflowProtocolList, errorsList)
        else:
            self._continueWorkflow(workflowProtocolList, errorsList)
        return errorsList

    def launchProtocol(self, protocol, wait=False, scheduled=False,
                       force=False):
        """ In this function the action of launching a protocol
        will be initiated. Actions done here are:

        1. Store the protocol and assign name and working dir
        2. Create the working dir and also the protocol independent db
        3. Call the launch method in protocol.job to handle submission:
            mpi, thread, queue,
            and also take care if the execution is remotely.

        If the protocol has some prerequisites (other protocols that
        needs to be finished first), it will be scheduled.

        :param protocol: Protocol instance to launch
        :param wait: Optional. If true, this method
            will wait until execution is finished. Used in tests.
        :param scheduled: Optional. If true, run.db and paths
            already exist and are preserved.
        :param force: Optional. If true, launch is forced, regardless
            latter dependent executions. Used when restarting many protocols a once.

        """
        if protocol.getPrerequisites() and not scheduled:
            return self.scheduleProtocol(protocol)

        isRestart = protocol.getRunMode() == MODE_RESTART

        if not force:
            if (not protocol.isInteractive() and not protocol.isInStreaming()) or isRestart:
                self._checkModificationAllowed([protocol],
                                               'Cannot RE-LAUNCH protocol')

        protocol.setStatus(pwprot.STATUS_LAUNCHED)
        self._setupProtocol(protocol)

        # Prepare a separate db for this run if not from schedule jobs
        # Scheduled protocols will load the project db from the run.db file,
        # so there is no need to copy the database

        if not scheduled:
            protocol.makePathsAndClean()  # Create working dir if necessary
            # Delete the relations created by this protocol
            if isRestart:
                self.mapper.deleteRelations(self)
            self.mapper.commit()

            # NOTE: now we are simply copying the entire project db, this can be
            # changed later to only create a subset of the db need for the run
            pwutils.path.copyFile(self.dbPath, protocol.getDbPath())

        # Launch the protocol, the jobId should be set after this call
        jobId = pwprot.launch(protocol, wait)
        if jobId is None or jobId == UNKNOWN_JOBID:
            protocol.setStatus(pwprot.STATUS_FAILED)

        # Commit changes
        if wait:  # This is only useful for launching tests...
            self._updateProtocol(protocol)
        else:
            self.mapper.store(protocol)
        self.mapper.commit()

    def scheduleProtocol(self, protocol, prerequisites=[], initialSleepTime=0):
        """ Schedule a new protocol that will run when the input data
        is available and the prerequisites are finished.

        :param protocol: the protocol that will be scheduled.
        :param prerequisites: a list with protocols ids that the scheduled
            protocol will wait for.
        :param initialSleepTime: number of seconds to wait before
            checking input's availability

        """
        isRestart = protocol.getRunMode() == MODE_RESTART

        protocol.setStatus(pwprot.STATUS_SCHEDULED)
        protocol.addPrerequisites(*prerequisites)

        self._setupProtocol(protocol)
        protocol.makePathsAndClean()  # Create working dir if necessary
        # Delete the relations created by this protocol if any
        if isRestart:
            self.mapper.deleteRelations(self)
        self.mapper.commit()

        # Prepare a separate db for this run
        # NOTE: now we are simply copying the entire project db, this can be
        # changed later to only create a subset of the db need for the run
        pwutils.path.copyFile(self.dbPath, protocol.getDbPath())
        # Launch the protocol, the jobId should be set after this call
        pwprot.schedule(protocol, initialSleepTime=initialSleepTime)
        self.mapper.store(protocol)
        self.mapper.commit()

    def _updateProtocol(self, protocol, tries=0, checkPid=False,
                        skipUpdatedProtocols=True):

        # If this is read only exit
        if self.openedAsReadOnly():
            return pw.NOT_UPDATED_READ_ONLY

        try:

            # Backup the values of 'jobId', 'label' and 'comment'
            # to be restored after the .copy
            jobId = protocol.getJobId()
            label = protocol.getObjLabel()
            comment = protocol.getObjComment()

            if skipUpdatedProtocols:
                # If we are already updated, comparing timestamps
                if pwprot.isProtocolUpToDate(protocol):
                    return pw.NOT_UPDATED_UNNECESSARY


            # If the protocol database has ....
            #  Comparing date will not work unless we have a reliable
            # lastModificationDate of a protocol in the project.sqlite
            # TODO: when launching remote protocols, the db should be
            # TODO: retrieved in a different way.
            prot2 = pwprot.getProtocolFromDb(self.path,
                                             protocol.getDbPath(),
                                             protocol.getObjId())

            # Capture the db timestamp before loading.
            lastUpdateTime = pwutils.getFileLastModificationDate(protocol.getDbPath())

            # Copy is only working for db restored objects
            protocol.setMapper(self.mapper)

            localOutputs = list(protocol._outputs)
            protocol.copy(prot2, copyId=False, excludeInputs=True)

            # merge outputs: This is necessary when outputs are added from the GUI
            # e.g.: adding coordinates from analyze result and protocol is active (interactive).
            for attr in localOutputs:
                if attr not in protocol._outputs:
                    protocol._outputs.append(attr)

            # Restore backup values
            protocol.setJobId(jobId)
            protocol.setObjLabel(label)
            protocol.setObjComment(comment)
            # Use the run.db timestamp instead of the system TS to prevent
            # possible inconsistencies.
            protocol.lastUpdateTimeStamp.set(lastUpdateTime)

            # Check pid at the end, once updated
            if checkPid:
                self.checkPid(protocol)


            self.mapper.store(protocol)

            # Close DB connections
            prot2.getProject().closeMapper()
            prot2.closeMappers()

        except Exception as ex:
            if tries == 3:  # 3 tries have been failed
                traceback.print_exc()
                # If any problem happens, the protocol will be marked
                # with a FAILED status
                try:
                    protocol.setFailed(str(ex))
                    self.mapper.store(protocol)
                except Exception:
                    pass
                return pw.NOT_UPDATED_ERROR
            else:
                logger.warning("Couldn't update protocol %s(jobId=%s) from it's own database. ERROR: %s, attempt=%d"
                             % (protocol.getObjName(), jobId, ex, tries))
                time.sleep(0.5)
                self._updateProtocol(protocol, tries + 1)


        return pw.PROTOCOL_UPDATED

    def stopProtocol(self, protocol):
        """ Stop a running protocol """
        try:
            if protocol.getStatus() in ACTIVE_STATUS:
                pwprot.stop(protocol)
        except Exception as e:
            logger.error("Couldn't stop the protocol: %s" % e)
            raise
        finally:
            protocol.setAborted()
            protocol.setMapper(self.createMapper(protocol.getDbPath()))
            protocol._store()
            self._storeProtocol(protocol)
            protocol.getMapper().close()

    def resetProtocol(self, protocol):
        """ Stop a running protocol """
        try:
            if protocol.getStatus() in ACTIVE_STATUS:
                pwprot.stop(protocol)
        except Exception:
            raise
        finally:
            protocol.setSaved()
            protocol.runMode.set(MODE_RESTART)
            protocol._store()
            self._storeProtocol(protocol)
            protocol.makePathsAndClean()  # Create working dir if necessary
            protocol._store()
            self._storeProtocol(protocol)

    def continueProtocol(self, protocol):
        """ This function should be called 
        to mark a protocol that have an interactive step
        waiting for approval that can continue
        """
        protocol.continueFromInteractive()
        self.launchProtocol(protocol)

    def __protocolInList(self, prot, protocols):
        """ Check if a protocol is in a list comparing the ids. """
        for p in protocols:
            if p.getObjId() == prot.getObjId():
                return True
        return False

    def __validDependency(self, prot, child, protocols):
        """ Check if the given child is a true dependency of the protocol
        in order to avoid any modification.
        """
        return (not self.__protocolInList(child, protocols) and
                not child.isSaved() and not child.isScheduled())

    def _getProtocolsDependencies(self, protocols):
        error = ''
        runsGraph = self.getRunsGraph()
        for prot in protocols:
            node = runsGraph.getNode(prot.strId())
            if node:
                childs = [node.run for node in node.getChilds() if
                          self.__validDependency(prot, node.run, protocols)]
                if childs:
                    deps = [' ' + c.getRunName() for c in childs]
                    error += '\n *%s* is referenced from:\n   - ' % prot.getRunName()
                    error += '\n   - '.join(deps)
        return error

    def _checkProtocolsDependencies(self, protocols, msg):
        """ Check if the protocols have dependencies.
        This method is used before delete or save protocols to be sure
        it is not referenced from other runs. (an Exception is raised)
        Params:
             protocols: protocol list to be analyzed.
             msg: String message to be prefixed to Exception error.
        """
        # Check if the protocol have any dependencies
        error = self._getProtocolsDependencies(protocols)
        if error:
            raise ModificationNotAllowedException(msg + error)

    def _checkModificationAllowed(self, protocols, msg):
        """ Check if any modification operation is allowed for
        this group of protocols. 
        """
        if self.openedAsReadOnly():
            raise Exception(msg + " Running in READ-ONLY mode.")

        self._checkProtocolsDependencies(protocols, msg)

    def _getWorkflowFromProtocol(self, protocol, fixProtParam=True):
        """
        This function get the workflow from "protocol" and determine the
        protocol level into the graph. Also, checks if there are active
        protocols excluding interactive protocols.
        """
        activeProtList = []
        configuredProtList = {}
        auxProtList = []
        # store the protocol and your level into the workflow
        configuredProtList[protocol.getObjId()] = [protocol, 0]
        auxProtList.append(protocol.getObjId())
        runGraph = self.getRunsGraph()

        while auxProtList:
            protocol = runGraph.getNode(str(auxProtList.pop(0))).run
            level = configuredProtList[protocol.getObjId()][1] + 1
            if fixProtParam:
                self._fixProtParamsConfiguration(protocol)
            if protocol.isActive() and protocol.getStatus() != STATUS_INTERACTIVE:
                activeProtList.append(protocol)
            node = runGraph.getNode(protocol.strId())
            dependencies = [node.run for node in node.getChilds()]
            for dep in dependencies:
                if not dep.getObjId() in auxProtList:
                    auxProtList.append(dep.getObjId())
                if not dep.getObjId() in configuredProtList.keys():
                    configuredProtList[dep.getObjId()] = [dep, level]
                elif level > configuredProtList[dep.getObjId()][1]:
                    configuredProtList[dep.getObjId()][1] = level

        return configuredProtList, activeProtList

    def deleteProtocol(self, *protocols):
        self._checkModificationAllowed(protocols, 'Cannot DELETE protocols')

        for prot in protocols:
            # Delete the relations created by this protocol
            self.mapper.deleteRelations(prot)
            # Delete from protocol from database
            self.mapper.delete(prot)
            wd = prot.workingDir.get()

            if wd.startswith(PROJECT_RUNS):
                prot.cleanWorkingDir()
            else:
                logger.info("Can't delete protocol %s. Its workingDir %s does not starts with %s " % (prot, wd, PROJECT_RUNS))

        self.mapper.commit()

    def deleteProtocolOutput(self, protocol, output):
        """ Delete a given object from the project.
        Usually to clean up some outputs.
        """
        node = self.getRunsGraph().getNode(protocol.strId())
        deps = []

        for node in node.getChilds():
            for _, inputObj in node.run.iterInputAttributes():
                value = inputObj.get()
                if (value is not None and
                        value.getObjId() == output.getObjId() and
                        not node.run.isSaved()):
                    deps.append(node.run)

        if deps:
            error = 'Cannot DELETE Object, it is referenced from:'
            for d in deps:
                error += '\n - %s' % d.getRunName()
            raise Exception(error)
        else:
            protocol.deleteOutput(output)
            pwutils.path.copyFile(self.dbPath, protocol.getDbPath())

    def __setProtocolLabel(self, newProt):
        """ Set a readable label to a newly created protocol.
        We will try to find another existing protocol with the default label
        and then use an incremental labeling in parenthesis (<number>++)
        """
        defaultLabel = newProt.getClassLabel()
        maxSuffix = 0

        for prot in self.getRuns(iterate=True, refresh=False):
            otherProtLabel = prot.getObjLabel()
            m = REGEX_NUMBER_ENDING.match(otherProtLabel)
            if m and m.groupdict()['prefix'].strip() == defaultLabel:
                stringSuffix = m.groupdict()['number'].strip('(').strip(')')
                try:
                    maxSuffix = max(int(stringSuffix), maxSuffix)
                except:
                    logger.error("Couldn't set protocol's label. %s" % stringSuffix)
            elif otherProtLabel == defaultLabel:  # When only we have the prefix,
                maxSuffix = max(1, maxSuffix)     # this REGEX don't match.

        if maxSuffix:
            protLabel = '%s (%d)' % (defaultLabel, maxSuffix+1)
        else:
            protLabel = defaultLabel

        newProt.setObjLabel(protLabel)

    def newProtocol(self, protocolClass, **kwargs):
        """ Create a new protocol from a given class. """
        newProt = protocolClass(project=self, **kwargs)
        # Only set a default label to the protocol if is was not
        # set through the kwargs
        if not newProt.getObjLabel():
            self.__setProtocolLabel(newProt)

        newProt.setMapper(self.mapper)
        newProt.setProject(self)

        return newProt

    def __getIOMatches(self, node, childNode):
        """ Check if some output of node is used as input in childNode.
        Return the list of attribute names that matches.
        Used from self.copyProtocol
        """
        matches = []
        for iKey, iAttr in childNode.run.iterInputAttributes():
            # As this point iAttr should be always a Pointer that 
            # points to the output of other protocol
            if iAttr.getObjValue() is node.run:
                oKey = iAttr.getExtended()
                matches.append((oKey, iKey))
            else:
                for oKey, oAttr in node.run.iterOutputAttributes():
                    # If node output is "real" and iAttr is still just a pointer
                    # the iAttr.get() will return None
                    pointed = iAttr.get()
                    if pointed is not None and oAttr.getObjId() == pointed.getObjId():
                        matches.append((oKey, iKey))

        return matches

    def __cloneProtocol(self, protocol):
        """ Make a copy of the protocol parameters, not outputs. 
            We will label the new protocol with the same name adding the 
            parenthesis as follow -> (copy) -> (copy 2) -> (copy 3)
        """
        newProt = self.newProtocol(protocol.getClass())
        oldProtName = protocol.getRunName()
        maxSuffix = 0

        # if '(copy...' suffix is not in the old name, we add it in the new name
        # and setting the newnumber
        mOld = REGEX_NUMBER_ENDING_CP.match(oldProtName)
        if mOld:
            newProtPrefix = mOld.groupdict()['prefix']
            if mOld.groupdict()['number'] == '':
                oldNumber = 1
            else:
                oldNumber = int(mOld.groupdict()['number'])
        else:
            newProtPrefix = oldProtName + ' (copy'
            oldNumber = 0
        newNumber = oldNumber + 1

        # looking for "<old name> (copy" prefixes in the project and
        # setting the newNumber as the maximum+1
        for prot in self.getRuns(iterate=True, refresh=False):
            otherProtLabel = prot.getObjLabel()
            mOther = REGEX_NUMBER_ENDING_CP.match(otherProtLabel)
            if mOther and mOther.groupdict()['prefix'] == newProtPrefix:
                stringSuffix = mOther.groupdict()['number']
                if stringSuffix == '':
                    stringSuffix = 1
                maxSuffix = max(maxSuffix, int(stringSuffix))
                if newNumber <= maxSuffix:
                    newNumber = maxSuffix + 1

        # building the new name
        if newNumber == 1:
            newProtLabel = newProtPrefix + ')'
        else:
            newProtLabel = '%s %d)' % (newProtPrefix, newNumber)

        newProt.setObjLabel(newProtLabel)
        newProt.copyDefinitionAttributes(protocol)
        newProt.copyAttributes(protocol, 'hostName', '_useQueue', '_queueParams')
        newProt.runMode.set(MODE_RESTART)

        return newProt

    def copyProtocol(self, protocol):
        """ Make a copy of the protocol,
        Return a new instance with copied values. """
        result = None

        if isinstance(protocol, pwprot.Protocol):
            result = self.__cloneProtocol(protocol)

        elif isinstance(protocol, list):
            # Handle the copy of a list of protocols
            # for this case we need to update the references of input/outputs
            newDict = {}

            for prot in protocol:
                newProt = self.__cloneProtocol(prot)
                newDict[prot.getObjId()] = newProt
                self.saveProtocol(newProt)

            g = self.getRunsGraph()

            for prot in protocol:
                node = g.getNode(prot.strId())
                newProt = newDict[prot.getObjId()]

                for childNode in node.getChilds():
                    newChildProt = newDict.get(childNode.run.getObjId(), None)

                    if newChildProt:
                        # Get the matches between outputs/inputs of
                        # node and childNode
                        matches = self.__getIOMatches(node, childNode)
                        # For each match, set the pointer and the extend
                        # attribute to reproduce the dependencies in the
                        # new workflow
                        for oKey, iKey in matches:
                            childPointer = getattr(newChildProt, iKey)

                            # Scalar with pointer case: If is a scalar with a pointer
                            if isinstance(childPointer, pwobj.Scalar) and childPointer.hasPointer():
                              # In this case childPointer becomes the contained Pointer
                              childPointer = childPointer.getPointer()

                            elif isinstance(childPointer, pwobj.PointerList):
                                for p in childPointer:
                                    if p.getObjValue().getObjId() == prot.getObjId():
                                        childPointer = p
                            childPointer.set(newProt)
                            childPointer.setExtended(oKey)
                        self.mapper.store(newChildProt)

            self.mapper.commit()
        else:
            raise Exception("Project.copyProtocol: invalid input protocol ' "
                            "'type '%s'." % type(protocol))

        return result

    def getProtocolsDict(self, protocols=None, namesOnly=False):
        """ Creates a dict with the information of the given protocols.

        :param protocols: list of protocols or None to include all.
        :param namesOnly: the output list will contain only the protocol names.

        """
        protocols = protocols or self.getRuns()

        # If the nameOnly, we will simply return a json list with their names
        if namesOnly:
            return {i: prot.getClassName() for i, prot in enumerate(protocols)}

        # Handle the copy of a list of protocols
        # for this case we need to update the references of input/outputs
        newDict = OrderedDict()

        for prot in protocols:
            newDict[prot.getObjId()] = prot.getDefinitionDict()

        g = self.getRunsGraph()

        for prot in protocols:
            protId = prot.getObjId()
            node = g.getNode(prot.strId())

            for childNode in node.getChilds():
                childId = childNode.run.getObjId()
                childProt = childNode.run
                if childId in newDict:
                    childDict = newDict[childId]
                    # Get the matches between outputs/inputs of
                    # node and childNode
                    matches = self.__getIOMatches(node, childNode)
                    for oKey, iKey in matches:
                        inputAttr = getattr(childProt, iKey)
                        if isinstance(inputAttr, pwobj.PointerList):
                            childDict[iKey] = [p.getUniqueId() for p in
                                               inputAttr]
                        else:
                            childDict[iKey] = '%s.%s' % (
                                protId, oKey)  # equivalent to pointer.getUniqueId

        return newDict

    def getProtocolsJson(self, protocols=None, namesOnly=False):
        """
        Wraps getProtocolsDict to get a json string

        :param protocols: list of protocols or None to include all.
        :param namesOnly: the output list will contain only the protocol names.

        """
        newDict = self.getProtocolsDict(protocols=protocols, namesOnly=namesOnly)
        return json.dumps(list(newDict.values()),
                          indent=4, separators=(',', ': '))

    def exportProtocols(self, protocols, filename):
        """ Create a text json file with the info
        to import the workflow into another project.
        This method is very similar to copyProtocol

        :param protocols: a list of protocols to export.
        :param filename: the filename where to write the workflow.

        """
        jsonStr = self.getProtocolsJson(protocols)
        f = open(filename, 'w')
        f.write(jsonStr)
        f.close()

    def loadProtocols(self, filename=None, jsonStr=None):
        """ Load protocols generated in the same format as self.exportProtocols.

        :param filename: the path of the file where to read the workflow.
        :param jsonStr: Not used.

        Note: either filename or jsonStr should be not None.

        """
        f = open(filename)
        importDir = os.path.dirname(filename)
        protocolsList = json.load(f)

        emProtocols = self._domain.getProtocols()
        newDict = OrderedDict()

        # First iteration: create all protocols and setup parameters
        for i, protDict in enumerate(protocolsList):
            protClassName = protDict['object.className']
            protId = protDict['object.id']
            protClass = emProtocols.get(protClassName, None)

            if protClass is None:
                logger.error("Protocol with class name '%s' not found. Are you missing its plugin?." % protClassName)
            else:
                protLabel = protDict.get('object.label', None)
                prot = self.newProtocol(protClass,
                                        objLabel=protLabel,
                                        objComment=protDict.get('object.comment', None))
                protocolsList[i] = prot.processImportDict(protDict, importDir)

                prot._useQueue.set(protDict.get('_useQueue', False))
                prot._queueParams.set(protDict.get('_queueParams', None))
                prot._prerequisites.set(protDict.get('_prerequisites', None))
                prot.forceSchedule.set(protDict.get('forceSchedule', False))
                newDict[protId] = prot
                self.saveProtocol(prot)

        # Second iteration: update pointers values
        def _setPointer(pointer, value):
            # Properly setup the pointer value checking if the 
            # id is already present in the dictionary
            # Value to pointers could be None: Partial workflows
            if value:
                parts = value.split('.')
                target = newDict.get(parts[0], None)
                pointer.set(target)
                if not pointer.pointsNone():
                    pointer.setExtendedParts(parts[1:])

        def _setPrerequisites(prot):
            prerequisites = prot.getPrerequisites()
            if prerequisites:
                newPrerequisites = []
                for prerequisite in prerequisites:
                    if prerequisite in newDict:
                        newProtId = newDict[prerequisite].getObjId()
                        newPrerequisites.append(newProtId)
                    else:
                        logger.info('"Wait for" id %s missing: ignored.' % prerequisite)
                prot._prerequisites.set(newPrerequisites)

        for protDict in protocolsList:
            protId = protDict['object.id']

            if protId in newDict:
                prot = newDict[protId]
                _setPrerequisites(prot)
                for paramName, attr in prot.iterDefinitionAttributes():
                    if paramName in protDict:
                        # If the attribute is a pointer, we should look
                        # if the id is already in the dictionary and 
                        # set the extended property
                        if attr.isPointer():
                            _setPointer(attr, protDict[paramName])
                        # This case is similar to Pointer, but the values
                        # is a list and we will setup a pointer for each value
                        elif isinstance(attr, pwobj.PointerList):
                            attribute = protDict[paramName]
                            if attribute is None:
                                continue
                            for value in attribute:
                                p = pwobj.Pointer()
                                _setPointer(p, value)
                                attr.append(p)
                        # For "normal" parameters we just set the string value
                        else:
                            try:
                                attr.set(protDict[paramName])
                            # Case for Scalars with pointers. So far this will work for Numbers. With Strings (still there are no current examples)
                            # We will need something different to test if the value look like a pointer: regex? ####.text
                            except ValueError as e:
                                newPointer = pwobj.Pointer()
                                _setPointer(newPointer, protDict[paramName])
                                attr.setPointer(newPointer)

                self.mapper.store(prot)

        f.close()
        self.mapper.commit()

        return newDict

    def saveProtocol(self, protocol):
        self._checkModificationAllowed([protocol], 'Cannot SAVE protocol')

        if (protocol.isRunning() or protocol.isFinished()
                or protocol.isLaunched()):
            raise ModificationNotAllowedException('Cannot SAVE a protocol that is %s. '
                            'Copy it instead.' % protocol.getStatus())

        protocol.setStatus(pwprot.STATUS_SAVED)
        if protocol.hasObjId():
            self._storeProtocol(protocol)
        else:
            self._setupProtocol(protocol)

    def getProtocol(self, protId):
        protocol = self.mapper.selectById(protId)

        if not isinstance(protocol, pwprot.Protocol):
            raise Exception('>>> ERROR: Invalid protocol id: %d' % protId)

        self._setProtocolMapper(protocol)

        return protocol

    # FIXME: this function just return if a given object exists, not
    # if it is a protocol, so it is incorrect judging by the name
    # Moreover, a more consistent name (comparing to similar methods)
    # would be: hasProtocol
    def doesProtocolExists(self, protId):
        return self.mapper.exists(protId)

    def getProtocolsByClass(self, className):
        return self.mapper.selectByClass(className)

    def getObject(self, objId):
        """ Retrieve an object from the db given its id. """
        return self.mapper.selectById(objId)

    def _setHostConfig(self, protocol):
        """ Set the appropriate host config to the protocol
        give its value of 'hostname'
        """
        hostName = protocol.getHostName()
        hostConfig = self.getHostConfig(hostName)
        protocol.setHostConfig(hostConfig)

    def _storeProtocol(self, protocol):
        # Read only mode
        if not self.openedAsReadOnly():
            self.mapper.store(protocol)
            self.mapper.commit()

    def _setProtocolMapper(self, protocol):
        """ Set the project and mapper to the protocol. """

        # Tolerate loading errors. For support.
        # When only having the sqlite, sometime there are exceptions here
        # due to the absence of a set.
        from pyworkflow.mapper.sqlite import SqliteFlatMapperException
        try:

            protocol.setProject(self)
            protocol.setMapper(self.mapper)
            self._setHostConfig(protocol)

        except SqliteFlatMapperException:
            protocol.addSummaryWarning(
                "*Protocol loading problem*: A set related to this "
                "protocol couldn't be loaded.")

    def _setupProtocol(self, protocol):
        """Insert a new protocol instance in the database"""

        # Read only mode
        if not self.openedAsReadOnly():
            self._storeProtocol(protocol)  # Store first to get a proper id
            # Set important properties of the protocol
            workingDir = self.getProtWorkingDir(protocol)
            self._setProtocolMapper(protocol)

            protocol.setWorkingDir(self.getPath(PROJECT_RUNS, workingDir))
            # Update with changes
            self._storeProtocol(protocol)

    @staticmethod
    def getProtWorkingDir(protocol):
        """
        Return the protocol working directory
        """
        return "%06d_%s" % (protocol.getObjId(), protocol.getClassName())

    def getRuns(self, iterate=False, refresh=True, checkPids=False):
        """ Return the existing protocol runs in the project. 
        """
        if self.runs is None or refresh:
            # Close db open connections to db files
            if self.runs is not None:
                for r in self.runs:
                    r.closeMappers()

            # Use new selectAll Batch
            # self.runs = self.mapper.selectAll(iterate=False,
            #               objectFilter=lambda o: isinstance(o, pwprot.Protocol))
            self.runs = self.mapper.selectAllBatch(objectFilter=lambda o: isinstance(o, pwprot.Protocol))

            # Invalidate _runsGraph because the runs are updated
            self._runsGraph = None

            for r in self.runs:

                self._setProtocolMapper(r)

                # Check for run warnings
                r.checkSummaryWarnings()

                # Update nodes that are running and were not invoked
                # by other protocols
                if r.isActive():
                    if not r.isChild():
                        self._updateProtocol(r, checkPid=checkPids)

                self._annotateLastRunTime(r.endTime)

            self.mapper.commit()

        return self.runs

    def _annotateLastRunTime(self, protLastTS):
        """ Sets _lastRunTime for the project if it is after current _lastRunTime"""
        try:
            if protLastTS is None:
                return

            if self._lastRunTime is None:
                self._lastRunTime = protLastTS
            elif self._lastRunTime.datetime() < protLastTS.datetime():
                self._lastRunTime = protLastTS
        except Exception as e:
            return

    def needRefresh(self):
        """ True if any run is active and its timestamp is older than its
        corresponding runs.db
        NOTE: If an external script changes the DB this will fail. It uses
        only in memory objects."""
        for run in self.runs:
            if run.isActive():
                if not pwprot.isProtocolUpToDate(run):
                    return True
        return False

    def checkPid(self, protocol):
        """ Check if a running protocol is still alive or not.
        The check will only be done for protocols that have not been sent
        to a queue system.
        """
        from pyworkflow.protocol.launch import _runsLocally
        pid = protocol.getPid()

        if pid == 0:
            return

        # Include running and scheduling ones
        # Exclude interactive protocols
        # NOTE: This may be happening even with successfully finished protocols
        # which PID is gone.
        if (protocol.isActive() and not protocol.isInteractive() and _runsLocally(protocol)
            and not protocol.useQueue()
                and not pwutils.isProcessAlive(pid)):
            protocol.setFailed("Process %s not found running on the machine. "
                               "It probably has died or been killed without "
                               "reporting the status to Scipion. Logs might "
                               "have information about what happened to this "
                               "process." % pid)

    def iterSubclasses(self, classesName, objectFilter=None):
        """ Retrieve all objects from the project that are instances
            of any of the classes in classesName list.
        Params: 
            classesName: String with commas separated values of classes name. 
            objectFilter: a filter function to discard some of the retrieved
            objects."""
        for objClass in classesName.split(","):
            for obj in self.mapper.selectByClass(objClass.strip(), iterate=True,
                                                 objectFilter=objectFilter):
                yield obj

    def getRunsGraph(self, refresh=False, checkPids=False):
        """ Build a graph taking into account the dependencies between
        different runs, ie. which outputs serves as inputs of other protocols. 
        """

        if refresh or self._runsGraph is None:
            runs = [r for r in self.getRuns(refresh=refresh, checkPids=checkPids)
                    if not r.isChild()]
            self._runsGraph = self.getGraphFromRuns(runs)

        return self._runsGraph

    def getGraphFromRuns(self, runs):
        """
        This function will build a dependencies graph from a set
        of given runs.

        :param runs: The input runs to build the graph
        :return: The graph taking into account run dependencies

        """
        outputDict = {}  # Store the output dict
        g = pwutils.Graph(rootName='PROJECT')

        for r in runs:
            n = g.createNode(r.strId())
            n.run = r
            n.setLabel(r.getRunName())
            outputDict[r.getObjId()] = n
            for _, attr in r.iterOutputAttributes():
                # mark this output as produced by r
                outputDict[attr.getObjId()] = n

        def _checkInputAttr(node, pointed):
            """ Check if an attr is registered as output"""
            if pointed is not None:
                pointedId = pointed.getObjId()

                if pointedId in outputDict:
                    parentNode = outputDict[pointedId]
                    if parentNode is node:
                        logger.warning("WARNING: Found a cyclic dependence from node "
                              "%s to itself, probably a bug. " % pointedId)
                    else:
                        parentNode.addChild(node)
                        return True
            return False

        for r in runs:
            node = g.getNode(r.strId())
            for _, attr in r.iterInputAttributes():
                if attr.hasValue():
                    pointed = attr.getObjValue()
                    # Only checking pointed object and its parent, if more
                    # levels we need to go up to get the correct dependencies
                    if not _checkInputAttr(node, pointed):
                        parent = self.mapper.getParent(pointed)
                        _checkInputAttr(node, parent)
        rootNode = g.getRoot()
        rootNode.run = None
        rootNode.label = "PROJECT"

        for n in g.getNodes():
            if n.isRoot() and n is not rootNode:
                rootNode.addChild(n)
        return g

    def _getRelationGraph(self, relation=pwobj.RELATION_SOURCE, refresh=False):
        """ Retrieve objects produced as outputs and
        make a graph taking into account the SOURCE relation. """
        relations = self.mapper.getRelationsByName(relation)
        g = pwutils.Graph(rootName='PROJECT')
        root = g.getRoot()
        root.pointer = None
        runs = self.getRuns(refresh=refresh)

        for r in runs:
            for paramName, attr in r.iterOutputAttributes():
                p = pwobj.Pointer(r, extended=paramName)
                node = g.createNode(p.getUniqueId(), attr.getNameId())
                node.pointer = p
                # The following alias if for backward compatibility
                p2 = pwobj.Pointer(attr)
                g.aliasNode(node, p2.getUniqueId())

        for rel in relations:
            pObj = self.getObject(rel[OBJECT_PARENT_ID])

            # Duplicated ...
            if pObj is None:
                logger.warning("Relation seems to point to a deleted object. "
                      "%s: %s" % (OBJECT_PARENT_ID, rel[OBJECT_PARENT_ID]))
                continue

            pExt = rel['object_parent_extended']
            pp = pwobj.Pointer(pObj, extended=pExt)

            if pObj is None or pp.get() is None:
                logger.error("project._getRelationGraph: pointer to parent is "
                      "None. IGNORING IT.\n")
                for key in rel.keys():
                    logger.info("%s: %s" % (key, rel[key]))

                continue

            pid = pp.getUniqueId()
            parent = g.getNode(pid)

            while not parent and pp.hasExtended():
                pp.removeExtended()
                parent = g.getNode(pp.getUniqueId())

            if not parent:
                logger.error("project._getRelationGraph: parent Node "
                      "is None: %s" % pid)
            else:
                cObj = self.getObject(rel['object_child_id'])
                cExt = rel['object_child_extended']

                if cObj is not None:
                    if cObj.isPointer():
                        cp = cObj
                        if cExt:
                            cp.setExtended(cExt)
                    else:
                        cp = pwobj.Pointer(cObj, extended=cExt)
                    child = g.getNode(cp.getUniqueId())

                    if not child:
                        logger.error("project._getRelationGraph: child Node "
                              "is None: %s." % cp.getUniqueId())
                        logger.error("   parent: %s" % pid)
                    else:
                        parent.addChild(child)
                else:
                    logger.error("project._getRelationGraph: child Obj "
                          "is None, id: %s " %  rel['object_child_id'])
                    logger.error("   parent: %s" % pid)

        for n in g.getNodes():
            if n.isRoot() and n is not root:
                root.addChild(n)

        return g

    def getSourceChilds(self, obj):
        """ Return all the objects have used obj
        as a source.
        """
        return self.mapper.getRelationChilds(pwobj.RELATION_SOURCE, obj)

    def getSourceParents(self, obj):
        """ Return all the objects that are SOURCE of this object.
        """
        return self.mapper.getRelationParents(pwobj.RELATION_SOURCE, obj)

    def getTransformGraph(self, refresh=False):
        """ Get the graph from the TRANSFORM relation. """
        if refresh or not self._transformGraph:
            self._transformGraph = self._getRelationGraph(pwobj.RELATION_TRANSFORM,
                                                          refresh)

        return self._transformGraph

    def getSourceGraph(self, refresh=False):
        """ Get the graph from the SOURCE relation. """
        if refresh or not self._sourceGraph:
            self._sourceGraph = self._getRelationGraph(pwobj.RELATION_SOURCE,
                                                       refresh)

        return self._sourceGraph

    def getRelatedObjects(self, relation, obj, direction=pwobj.RELATION_CHILDS,
                          refresh=False):
        """ Get all objects related to obj by a give relation.

        :param relation: the relation name to search for.
        :param obj: object from which the relation will be search,
            actually not only this, but all other objects connected
            to this one by the pwobj.RELATION_TRANSFORM.
        :parameter direction: Not used
        :param refresh: If True, cached objects will be refreshed

        """

        graph = self.getTransformGraph(refresh)
        relations = self.mapper.getRelationsByName(relation)
        connection = self._getConnectedObjects(obj, graph)

        objects = []
        objectsDict = {}

        for rel in relations:
            pObj = self.getObject(rel[OBJECT_PARENT_ID])

            if pObj is None:
                logger.warning("Relation seems to point to a deleted object. "
                      "%s: %s" % (OBJECT_PARENT_ID, rel[OBJECT_PARENT_ID]))
                continue
            pExt = rel['object_parent_extended']
            pp = pwobj.Pointer(pObj, extended=pExt)

            if pp.getUniqueId() in connection:
                cObj = self.getObject(rel['object_child_id'])
                cExt = rel['object_child_extended']
                cp = pwobj.Pointer(cObj, extended=cExt)
                if cp.hasValue() and cp.getUniqueId() not in objectsDict:
                    objects.append(cp)
                    objectsDict[cp.getUniqueId()] = True

        return objects

    def _getConnectedObjects(self, obj, graph):
        """ Given a TRANSFORM graph, return the elements that
        are connected to an object, either children, ancestors or siblings.
        """
        n = graph.getNode(obj.strId())
        # Get the oldest ancestor of a node, before reaching the root node
        while n is not None and not n.getParent().isRoot():
            n = n.getParent()

        connection = {}

        if n is not None:
            # Iterate recursively all descendants
            for node in n.iterChilds():
                connection[node.pointer.getUniqueId()] = True
                # Add also 
                connection[node.pointer.get().strId()] = True

        return connection

    def isReadOnly(self):
        if getattr(self, 'settings', None) is None:
            return False

        return self.settings.getReadOnly()

    def isInReadOnlyFolder(self):
        return self._isInReadOnlyFolder

    def openedAsReadOnly(self):
        return self.isReadOnly() or self.isInReadOnlyFolder()

    def setReadOnly(self, value):
        self.settings.setReadOnly(value)

    def fixLinks(self, searchDir):
        logger.info("Fixing project links. Searching at %s" % searchDir)
        runs = self.getRuns()

        for prot in runs:
            print (prot)
            broken = False
            if isinstance(prot, ProtImportBase) or prot.getClassName() == "ProtImportMovies":
                logger.info("Import detected")
                for _, attr in prot.iterOutputAttributes():
                    fn = attr.getFiles()
                    for f in attr.getFiles():
                        if ':' in f:
                            f = f.split(':')[0]

                        if not os.path.exists(f):
                            if not broken:
                                broken = True
                                logger.info("Found broken links in run: %s" %
                                      pwutils.magenta(prot.getRunName()))
                            logger.info("  Missing: %s" % pwutils.magenta(f))
                            if os.path.islink(f):
                                logger.info("    -> %s" % pwutils.red(os.path.realpath(f)))
                            newFile = pwutils.findFile(os.path.basename(f),
                                                       searchDir,
                                                       recursive=True)
                            if newFile:
                                logger.info("  Found file %s, creating link... %s" % (newFile,
                                    pwutils.green("   %s -> %s" % (f, newFile))))
                                pwutils.createAbsLink(newFile, f)

    @staticmethod
    def cleanProjectName(projectName):
        """ Cleans a project name to avoid common errors
        Use it whenever you want to get the final project name pyworkflow will endup.
        Spaces will be replaced by _ """
        return projectName.replace(" ", "_")


class MissingProjectDbException(Exception):
    pass


class ModificationNotAllowedException(Exception):
    pass
