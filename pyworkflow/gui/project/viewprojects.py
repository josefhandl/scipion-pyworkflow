# **************************************************************************
# *
# * Authors:     J.M. De la Rosa Trevin (delarosatrevin@scilifelab.se) [1]
# *
# * [1] SciLifeLab, Stockholm University
# *
# * This program is free software: you can redistribute it and/or modify
# * it under the terms of the GNU General Public License as published by
# * the Free Software Foundation, either version 3 of the License, or
# * (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program.  If not, see <https://www.gnu.org/licenses/>.
# *
# *  All comments concerning this program package may be sent to the
# *  e-mail address 'scipion@cnb.csic.es'
# *
# **************************************************************************
import logging
logger = logging.getLogger(__name__)

import os
import tkinter as tk
import tkinter.font as tkFont

import pyworkflow as pw
from pyworkflow.project import Project
from pyworkflow.utils.utils import prettyDate, prettyTime
from pyworkflow.utils.path import getHomePath

from pyworkflow.project import Manager
import pyworkflow.gui as pwgui
from pyworkflow.gui.text import TaggedText
from pyworkflow.gui.dialog import askString, askYesNo, showError

from pyworkflow.gui import Message, Window, cfgEntryBgColor
from pyworkflow.gui.browser import FileBrowserWindow
from pyworkflow.gui.widgets import IconButton, HotButton, Button
from pyworkflow.utils.properties import Icon


class ProjectsView(tk.Frame):
    _PROJ_CONTAINER = "projectsframe"

    def __init__(self, parent, windows, **args):
        tk.Frame.__init__(self, parent, bg='white', **args)
        self.windows = windows
        self.manager = windows.manager
        self.root = windows.root

        # tkFont.Font(size=12, family='verdana', weight='bold')
        bigSize = pwgui.cfgFontSize + 2
        smallSize = pwgui.cfgFontSize - 2
        fontName = pwgui.cfgFontName

        self.projNameFont = tkFont.Font(size=bigSize, family=fontName,
                                        weight='bold')
        self.projDateFont = tkFont.Font(size=smallSize, family=fontName)
        self.projDelFont = tkFont.Font(size=smallSize, family=fontName,
                                       weight='bold')
        self.manager = Manager()

        self.filter = tk.StringVar()
        self.filterBox = None
        self.addActionsFrame()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        text = TaggedText(self, width=40, height=15, bd=0, bg='white')
        text.grid(row=1, columnspan=2, column=0, sticky='news')

        self.createProjectList(text)
        text.setReadOnly(True)
        self.text = text
        self.filterBox.focus_set()

    def addActionsFrame(self):
        """ Add the "toolbar" for actions like create project, import
         project or filter"""
        # Add the create project button
        bg = "white"
        btnFrame = tk.Frame(self, bg=bg)
        btn = HotButton(btnFrame, text=Message.LABEL_CREATE_PROJECT,
                        font=self.projNameFont,
                        command=self._onCreateProject)
        btn.grid(row=0, column=0, sticky='nw', padx=10, pady=10)
        # Add the Import project button
        btn = Button(btnFrame, text=Message.LABEL_IMPORT_PROJECT,
                     font=self.projNameFont,
                     command=self._onImportProject)
        btn.grid(row=0, column=1, sticky='nw', padx=10, pady=10)
        btnFrame.grid(row=0, column=0, sticky='nw')

        # Add a filter box
        # Add the Import project button
        btn = tk.Label(btnFrame, bg=bg, text="Filter:", font=self.projNameFont)
        btn.grid(row=0, column=2, sticky='nse', padx=10, pady=10)
        self.filterBox = tk.Entry(btnFrame, font=self.projNameFont, textvariable=self.filter)
        self.filterBox.grid(row=0, column=3, sticky='ne', padx=10, pady=12)
        self.filterBox.bind('<Return>', self._onFilter)
        self.filterBox.bind('<KP_Enter>', self._onFilter)

    def createProjectList(self, text):
        """Load the list of projects"""
        r = 0
        text.setReadOnly(False)
        text.clear()
        parent = tk.Frame(text, bg='white', name=self._PROJ_CONTAINER)
        parent.columnconfigure(0, weight=1)
        colors = ['white', '#EAEBFF']
        for i, p in enumerate(self.manager.listProjects()):
            try:
                # Add creation time to project info
                # Add if it's a link
                p.index = "index%s" % i

                # Consider the filter
                if not self._doesProjectMatchFilter(p):
                    continue

                frame = self.createProjectLabel(parent, p, color=colors[i % 2])
                frame.grid(row=r, column=0, padx=10, pady=5, sticky='new')
                r += 1

            except Exception as ex:
                logger.error("Couldn't load project %s" % p.getName(), exc_info=True)

        text.window_create(tk.INSERT, window=parent)
        text.bindWidget(parent)
        text.setReadOnly(True)

    def createProjectLabel(self, parent, projInfo, color):
        frame = tk.Frame(parent, bg=color, name=projInfo.index)
        # ROW1
        # Project name
        label = tk.Label(frame, text=projInfo.projName, anchor='nw', bg=color,
                         justify=tk.LEFT, font=self.projNameFont, cursor='hand1', width=50)
        label.grid(row=0, column=0, padx=2, pady=2, sticky='nw')
        label.bind('<Button-1>', lambda e: self.openProject(projInfo.projName))

        # ROW2
        # Timestamp line
        dateMsg = '%s%s    %s%s' % (Message.LABEL_MODIFIED, prettyDate(projInfo.mTime),
                                    Message.LABEL_CREATED, prettyTime(projInfo.cTime, time=False))
        dateLabel = tk.Label(frame, text=dateMsg, font=self.projDateFont, bg=color)
        dateLabel.grid(row=1, column=0, sticky='nw')
        # Delete action
        delLabel = tk.Label(frame, text=Message.LABEL_DELETE_PROJECT, font=self.projDelFont, bg=color, cursor='hand1')
        delLabel.grid(row=1, column=1, padx=10)
        delLabel.bind('<Button-1>', lambda e: self.deleteProject(projInfo))
        # Rename action
        mvLabel = tk.Label(frame, text=Message.LABEL_RENAME_PROJECT, font=self.projDelFont, bg=color, cursor='hand1')
        mvLabel.grid(row=1, column=2)
        mvLabel.bind('<Button-1>', lambda e: self.renameProject(projInfo.projName))

        # ROW3
        if projInfo.isLink():
            linkMsg = 'link --> ' + projInfo.realPath()
            lblLink = tk.Label(frame, text=linkMsg, font=self.projDateFont, bg=color, fg='grey', justify=tk.LEFT)
            lblLink.grid(row=2, column=0, columnspan=3, sticky='w')

        return frame

    def createNewProject(self, projName, projLocation):
        proj = self.manager.createProject(projName, location=projLocation)
        self.createProjectList(self.text)
        self.openProject(proj.getShortName())

    def _onCreateProject(self, e=None):
        projWindow = ProjectCreateWindow("Create project", self)
        projWindow.show()

    def _onImportProject(self, e=None):
        importProjWindow = ProjectImportWindow("Import project", self)
        importProjWindow.show()

    def _onFilter(self, e=None):
        self.createProjectList(self.text)

    def _setFocusToList(self, e=None):
        self.text.focus_set()

    def _doesProjectMatchFilter(self, project):
        """ Returns true if the project matches the filter"""
        # Lets' use the name anc creation date for now:
        searchString = "~".join([project.getName().lower(),
                                 prettyDate(project.mTime),
                                 prettyTime(project.cTime, time=False)])

        return self.filter.get().lower() in searchString

    def importProject(self, projLocation, copyFiles, projName, searchLocation):

        self.manager.importProject(projLocation, copyFiles, projName, searchLocation)
        self.createProjectList(self.text)
        self.openProject(projName)

    def openProject(self, projName):
        from subprocess import Popen
        script = pw.join(pw.APPS, 'pw_project.py')
        Popen([pw.PYTHON, script, projName])

    def deleteProject(self, projInfo):

        projName = projInfo.projName

        if askYesNo(Message.TITLE_DELETE_PROJECT,
                    "Project *%s*. " % projName + Message.MESSAGE_DELETE_PROJECT, self.root):

            logger.info("User agreed to delete project %s" % projName)
            self.manager.deleteProject(projName)

            #Delete the frame
            self.text.children[self._PROJ_CONTAINER].children[projInfo.index].grid_forget()


    def renameProject(self, projName):
        newName = askString("Rename project %s" % projName, "Enter new name:", self.root)
        if not newName or newName == projName:
            return
        if self.manager.hasProject(newName):
            showError("Rename cancelled",
                      "Project name already exists: %s" % newName, self.root)
            return
        self.manager.renameProject(projName, newName)
        self.createProjectList(self.text)


class ProjectCreateWindow(Window):
    """ Windows to create a project. """

    def __init__(self, title, parent=None, weight=True, minsize=(400, 110),
                 icon=Icon.SCIPION_ICON, **args):
        """
         We assume the parent should be of ProjectsView
        """
        Window.__init__(self, title, parent.windows, weight=weight,
                        icon=icon, minsize=minsize, enableQueue=True)
        self.root['background'] = 'white'

        self.parent = parent
        self.projectsPath = self.parent.manager.PROJECTS
        self.projName = tk.StringVar()
        self.projName.set('')
        self.projLocation = tk.StringVar()
        self.projLocation.set(self.projectsPath)

        content = tk.Frame(self.root)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=3)
        content.config(bg='white')
        content.grid(row=0, column=0, sticky='news', padx=5, pady=5)

        # Info line
        labelInfo = tk.Label(content, text="Spaces will be replaced by underscores!", bg='white', bd=0)
        labelInfo.grid(row=0, sticky=tk.W, padx=5, pady=5)
        #  Project name line
        labelName = tk.Label(content, text=Message.LABEL_PROJECT + ' name', bg='white', bd=0)
        labelName.grid(row=1, sticky=tk.W, padx=5, pady=5)
        entryName = tk.Entry(content, bg=cfgEntryBgColor, width=20, textvariable=self.projName)
        entryName.grid(row=1, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=5)
        entryName.bind("<Return>", self._create)
        entryName.bind("<KP_Enter>", self._create)
        # Project location line
        labelLocation = tk.Label(content, text=Message.LABEL_PROJECT + ' location', bg='white', bd=0)
        labelLocation.grid(row=2, column=0, sticky='nw', padx=5, pady=5)

        self.entryBrowse = tk.Entry(content, bg=cfgEntryBgColor, width=40, textvariable=self.projLocation)
        self.entryBrowse.grid(row=2, column=1, sticky='nw', padx=5, pady=5)
        self.btnBrowse = IconButton(content, 'Browse', Icon.ACTION_BROWSE,
                                    highlightthickness=0, command=self._browsePath)
        self.btnBrowse.grid(row=2, column=2, sticky='e', padx=5, pady=5)

        self.initial_focus = entryName
        self.initial_focus.focus()

        btnFrame = tk.Frame(content)
        btnFrame.columnconfigure(0, weight=1)
        btnFrame.grid(row=3, column=0, sticky='sew', padx=5, pady=(0, 5), columnspan=2)
        btnFrame.config(bg='white')

        # Create buttons
        btnCreate = HotButton(btnFrame, 'Create', Icon.BUTTON_SELECT, command=self._create)
        btnCreate.grid(row=0, column=0, sticky='e', padx=5, pady=5)
        btnCancel = Button(btnFrame, 'Cancel', Icon.BUTTON_CANCEL, command=self.close)
        btnCancel.grid(row=0, column=1, sticky='e', padx=5, pady=5)

    def _browsePath(self, e=None):
        def onSelect(obj):
            self.projLocation.set(obj.getPath())

        v = self.projLocation.get().strip()
        path = None
        if v:
            v = os.path.dirname(v)
            if os.path.exists(v):
                path = v
        if not path:
            path = self.projectsPath

        browser = FileBrowserWindow("Browsing", self, path=path, onSelect=onSelect, onlyFolders=True)
        browser.show()

    def _create(self, e=None):
        projName = self.projName.get().strip()
        projLocation = self.projLocation.get().strip()

        # Validate that project name is not empty
        if not projName:
            showError("Validation error", "Project name is empty", self.root)
        # Validate that project location is not empty
        elif not projLocation:
            showError("Validation error", "Project location is empty", self.root)
        # Validate that project location exists
        elif not os.path.exists(projLocation):
            showError("Validation error", "Project location does not exist", self.root)
        # Validate that project location is a directory
        elif not os.path.isdir(projLocation):
            showError("Validation error", "Project location is not a directory", self.root)
        # Validate that project path (location + name) does not exists
        elif os.path.exists(os.path.join(projLocation, projName)):
            showError("Validation error", "Project path already exists", self.root)
        else:
            self.parent.createNewProject(projName, projLocation)
            self.close()


class ProjectImportWindow(Window):
    """ Windows to import a project. """

    def __init__(self, title, parent=None, weight=True, minsize=(400, 150),
                 icon=Icon.SCIPION_ICON, **args):
        """
         We assume the parent should be ProjectsView
        """
        Window.__init__(self, title, parent.windows, weight=weight,
                        icon=icon, minsize=minsize, enableQueue=True)
        self.root['background'] = 'white'
        self.parent = parent
        # Dirty hack, need to add a slash for the explorer to pick up the right default path.
        self.projectsPath = getHomePath() + "/"
        self.projLocation = tk.StringVar()
        self.projLocation.set(self.projectsPath)

        self.projName = tk.StringVar()
        self.projName.set('')

        self.searchLocation = tk.StringVar()
        self.searchLocation.set('')

        content = tk.Frame(self.root)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.config(bg='white')
        content.grid(row=0, column=0, sticky='news',
                     padx=5, pady=5)

        # Path explorer
        labelProjectLocation = tk.Label(content, text="Project location", bg='white', bd=0)
        labelProjectLocation.grid(row=0, column=0, sticky='nw', padx=5, pady=5)
        # it seems tk.Entry does not uses default font...grrrr!!
        self.entryBrowse = tk.Entry(content, bg=cfgEntryBgColor, width=40,
                                    textvariable=self.projLocation, font=self.font)
        self.entryBrowse.grid(row=0, column=1, sticky='nw', padx=5, pady=5)
        self.btnBrowse = IconButton(content, 'Browse', Icon.ACTION_BROWSE, highlightthickness=0,
                                    command=self._browseProjectLocation)
        self.btnBrowse.grid(row=0, column=2, sticky='e', padx=5, pady=5)

        # Copy files check
        labelCheck = tk.Label(content, text="Copy project", bg='white', borderwidth=0)
        labelCheck.grid(row=1, column=0, sticky='nw', padx=5, pady=5)

        self.tkCheckVar = tk.IntVar()
        btnCheck = tk.Checkbutton(content, variable=self.tkCheckVar, highlightthickness=0, activebackground='white',
                                  bg='white', bd=0)
        btnCheck.grid(row=1, column=1, sticky='nw', padx=0, pady=5)

        btnCopyHelp = IconButton(content, Message.LABEL_BUTTON_HELP, Icon.ACTION_HELP, highlightthickness=0,
                                 command=lambda: self.showInfo(
                                     'If checked, \"Project location\" will be copied. Otherwise a soft link to it will be created.'))
        btnCopyHelp.grid(row=1, column=3, sticky='e', padx=2, pady=2)

        # Project name
        labelName = tk.Label(content, text='Project name (Optional)', bg='white', bd=0)
        labelName.grid(row=2, column=0, sticky='nw', padx=5, pady=5)
        entryName = tk.Entry(content, bg='white', width=20, textvariable=self.projName, font=self.font)
        entryName.grid(row=2, column=1, sticky='nw', padx=5, pady=5)

        # Path to search for raw data and restore broken links.
        labelSearchLocation = tk.Label(content, text="Raw files location (Optional)", bg='white', bd=0)
        labelSearchLocation.grid(row=3, column=0, sticky='nw', padx=5, pady=5)

        self.entrySearchLocation = tk.Entry(content, bg='white', width=40,
                                            textvariable=self.searchLocation, font=self.font)
        self.entrySearchLocation.grid(row=3, column=1, sticky='nw', padx=5, pady=5)
        self.btnSearch = IconButton(content, 'Browse', Icon.ACTION_BROWSE,
                                    highlightthickness=0, command=self._browseSearchLocation)
        self.btnSearch.grid(row=3, column=2, sticky='e', padx=5, pady=5)
        btnSearchHelp = IconButton(content, Message.LABEL_BUTTON_HELP, Icon.ACTION_HELP, highlightthickness=0,
                                   command=lambda: self.showInfo(
                                       'Optional: Folder where raw files, binaries (movies, micrographs,..) can be found. Used to repair broken links.'))
        btnSearchHelp.grid(row=3, column=3, sticky='e', padx=2, pady=2)

        self.initial_focus = entryName
        self.initial_focus.focus()
        btnCheck.select()

        btnFrame = tk.Frame(content)
        btnFrame.columnconfigure(0, weight=1)
        btnFrame.grid(row=4, column=0, sticky='sew', padx=5, pady=(0, 5), columnspan=2)
        btnFrame.config(bg='white')

        # Create buttons
        btnSelect = HotButton(btnFrame, 'Import', Icon.BUTTON_SELECT, command=self._select)
        btnSelect.grid(row=0, column=0, sticky='e', padx=5, pady=5)
        btnCancel = Button(btnFrame, 'Cancel', Icon.BUTTON_CANCEL, command=self.close)
        btnCancel.grid(row=0, column=1, sticky='e', padx=5, pady=5)

    def _browseProjectLocation(self, e=None):
        self._browsePath(self.projLocation)

    def _browseSearchLocation(self, e=None):
        self._browsePath(self.searchLocation)

    def _browsePath(self, location):
        def onSelect(obj):
            location.set(obj.getPath())

        v = location.get().strip()
        path = None
        if v:
            v = os.path.dirname(v)
            if os.path.exists(v):
                path = v
        if not path:
            path = self.projectsPath

        browser = FileBrowserWindow("Browsing",
                                    self, path=path,
                                    onSelect=onSelect,
                                    onlyFolders=True)
        browser.show()

    def _select(self):
        projName = self.projName.get().strip()
        projLocation = self.projLocation.get().strip()
        copyFiles = self.tkCheckVar.get() != 0
        searchLocation = self.searchLocation.get().strip()
        manager = Manager()

        # If project name is empty we will use the same name as the source
        if not projName:
            projName = os.path.basename(projLocation)

        errorMessage = ''

        # Validate that project location is not empty
        if not projLocation:
            errorMessage = "Project location is empty\n"

        # Validate that project location exists
        elif not os.path.exists(projLocation):
            errorMessage += "Project location does not exist\n"

        # Validate that project location is a directory
        elif not os.path.isdir(projLocation):
            errorMessage += "Project location is not a directory\n"
        # Validate that the project location is a scipion project folder
        elif not os.path.exists(os.path.join(projLocation, Project.getDbName())):
            errorMessage += "Project location doesn't look like a scipion folder\n"

        # Validate that there isn't already a project with the same name
        if manager.hasProject(projName):
            errorMessage += "Project [%s] already exists\n" % projName

        # Validate that search location exists
        if searchLocation:
            if not os.path.exists(searchLocation):
                errorMessage += "Raw files location does not exist\n"
            # Validate that search location is a directory
            elif not os.path.isdir(searchLocation):
                errorMessage += "Raw files location is not a directory\n"

        if errorMessage:
            showError("Validation error", errorMessage, self.root)
        else:
            self.parent.importProject(projLocation, copyFiles, projName, searchLocation)
            self.close()
