"""The main window of the ComicTagger app"""

# Copyright 2012-2014 Anthony Beville

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import operator
import os
import pickle
import platform
import pprint
import re
import sys
import webbrowser
from typing import List, Optional, Union
from urllib.parse import urlparse

import natsort
from PyQt5 import QtCore, QtGui, QtNetwork, QtWidgets, uic

from comicapi import utils
from comicapi.comicarchive import ComicArchive, MetaDataStyle
from comicapi.comicinfoxml import ComicInfoXml
from comicapi.filenameparser import FileNameParser
from comicapi.genericmetadata import GenericMetadata
from comicapi.issuestring import IssueString
from comictaggerlib import ctversion
from comictaggerlib.autotagmatchwindow import AutoTagMatchWindow
from comictaggerlib.autotagprogresswindow import AutoTagProgressWindow
from comictaggerlib.autotagstartwindow import AutoTagStartWindow
from comictaggerlib.cbltransformer import CBLTransformer
from comictaggerlib.comicvinetalker import ComicVineTalker, ComicVineTalkerException
from comictaggerlib.coverimagewidget import CoverImageWidget
from comictaggerlib.crediteditorwindow import CreditEditorWindow
from comictaggerlib.exportwindow import ExportConflictOpts, ExportWindow
from comictaggerlib.fileselectionlist import FileInfo, FileSelectionList
from comictaggerlib.issueidentifier import IssueIdentifier
from comictaggerlib.logwindow import LogWindow
from comictaggerlib.optionalmsgdialog import OptionalMessageDialog
from comictaggerlib.pagebrowser import PageBrowserWindow
from comictaggerlib.pagelisteditor import PageListEditor
from comictaggerlib.renamewindow import RenameWindow
from comictaggerlib.resulttypes import IssueResult, MultipleMatch, OnlineMatchResults
from comictaggerlib.settings import ComicTaggerSettings
from comictaggerlib.settingswindow import SettingsWindow
from comictaggerlib.ui.qtutils import center_window_on_parent, reduce_widget_font_size
from comictaggerlib.versionchecker import VersionChecker
from comictaggerlib.volumeselectionwindow import VolumeSelectionWindow

logger = logging.getLogger(__name__)


def execute(f: callable):
    f()


class TaggerWindow(QtWidgets.QMainWindow):
    appName = "ComicTagger"
    version = ctversion.version

    def __init__(self, file_list, settings, parent=None, opts=None):
        super().__init__(parent)

        uic.loadUi(ComicTaggerSettings.get_ui_file("taggerwindow.ui"), self)
        self.settings = settings

        # prevent multiple instances
        socket = QtNetwork.QLocalSocket(self)
        socket.connectToServer(settings.install_id)
        alive = socket.waitForConnected(3000)
        if alive:
            print(f"Another application with key [{settings.install_id}] is already running")
            logger.info(f"Another application with key [{settings.install_id}] is already running")
            # send file list to other instance
            if file_list:
                socket.write(pickle.dumps(file_list))
                if not socket.waitForBytesWritten(3000):
                    print(socket.errorString())
            socket.disconnectFromServer()
            sys.exit()
        else:
            # listen on a socket to prevent multiple instances
            self.socketServer = QtNetwork.QLocalServer(self)
            self.socketServer.newConnection.connect(self.on_incoming_socket_connection)
            ok = self.socketServer.listen(settings.install_id)
            if not ok:
                if self.socketServer.serverError() == QtNetwork.QAbstractSocket.SocketError.AddressInUseError:
                    self.socketServer.removeServer(settings.install_id)
                    ok = self.socketServer.listen(settings.install_id)
                if not ok:
                    logger.error(
                        "Cannot start local socket with key [%s]. Reason: %s",
                        settings.install_id,
                        self.socketServer.errorString(),
                    )
                    sys.exit()

        self.archiveCoverWidget = CoverImageWidget(self.coverImageContainer, CoverImageWidget.ArchiveMode)
        grid_layout = QtWidgets.QGridLayout(self.coverImageContainer)
        grid_layout.addWidget(self.archiveCoverWidget)
        grid_layout.setContentsMargins(0, 0, 0, 0)

        self.page_list_editor = PageListEditor(self.tabPages)
        grid_layout = QtWidgets.QGridLayout(self.tabPages)
        grid_layout.addWidget(self.page_list_editor)

        self.fileSelectionList = FileSelectionList(self.widgetListHolder, self.settings, self.dirty_flag_verification)
        grid_layout = QtWidgets.QGridLayout(self.widgetListHolder)
        grid_layout.addWidget(self.fileSelectionList)

        self.fileSelectionList.selectionChanged.connect(self.file_list_selection_changed)
        self.fileSelectionList.listCleared.connect(self.file_list_cleared)
        self.fileSelectionList.set_sorting(
            self.settings.last_filelist_sorted_column, QtCore.Qt.SortOrder(self.settings.last_filelist_sorted_order)
        )

        # we can't specify relative font sizes in the UI designer, so
        # walk through all the labels in the main form, and make them
        # a smidge smaller TODO: there has to be a better way to do this
        for child in self.scrollAreaWidgetContents.children():
            if isinstance(child, QtWidgets.QLabel):
                f = child.font()
                if f.pointSize() > 10:
                    f.setPointSize(f.pointSize() - 2)
                f.setItalic(True)
                child.setFont(f)

        self.scrollAreaWidgetContents.adjustSize()

        self.setWindowIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("app.png")))
        # TODO: this needs to be looked at
        if opts is not None and opts.data_style is not None:
            # respect the command line option tag type
            settings.last_selected_save_data_style = opts.data_style
            settings.last_selected_load_data_style = opts.data_style

        self.save_data_style = settings.last_selected_save_data_style
        self.load_data_style = settings.last_selected_load_data_style

        self.setAcceptDrops(True)
        self.config_menus()
        self.statusBar()
        self.populate_combo_boxes()

        self.page_browser: Optional[PageBrowserWindow] = None
        self.comic_archive: Optional[ComicArchive] = None
        self.dirty_flag = False
        self.droppedFile = None
        self.page_loader = None
        self.droppedFiles = []
        self.metadata = GenericMetadata()
        self.atprogdialog: Optional[AutoTagProgressWindow] = None
        self.reset_app()

        # set up some basic field validators
        validator = QtGui.QIntValidator(1900, 2099, self)
        self.lePubYear.setValidator(validator)

        validator = QtGui.QIntValidator(1, 12, self)
        self.lePubMonth.setValidator(validator)

        # TODO: for now keep it simple, ideally we should check the full date
        validator = QtGui.QIntValidator(1, 31, self)
        self.lePubDay.setValidator(validator)

        validator = QtGui.QIntValidator(1, 99999, self)
        self.leIssueCount.setValidator(validator)
        self.leVolumeNum.setValidator(validator)
        self.leVolumeCount.setValidator(validator)
        self.leAltIssueNum.setValidator(validator)
        self.leAltIssueCount.setValidator(validator)

        # TODO set up an RE validator for issueNum that allows for all sorts of wacky things

        # tweak some control fonts
        reduce_widget_font_size(self.lblFilename, 1)
        reduce_widget_font_size(self.lblArchiveType)
        reduce_widget_font_size(self.lblTagList)
        reduce_widget_font_size(self.lblPageCount)

        # make sure some editable comboboxes don't take drop actions
        self.cbFormat.lineEdit().setAcceptDrops(False)
        self.cbMaturityRating.lineEdit().setAcceptDrops(False)

        # hook up the callbacks
        self.cbLoadDataStyle.currentIndexChanged.connect(self.set_load_data_style)
        self.cbSaveDataStyle.currentIndexChanged.connect(self.set_save_data_style)
        self.btnEditCredit.clicked.connect(self.edit_credit)
        self.btnAddCredit.clicked.connect(self.add_credit)
        self.btnRemoveCredit.clicked.connect(self.remove_credit)
        self.twCredits.cellDoubleClicked.connect(self.edit_credit)
        self.btnOpenWebLink.clicked.connect(self.open_web_link)
        self.connect_dirty_flag_signals()
        self.page_list_editor.modified.connect(self.set_dirty_flag)
        self.page_list_editor.firstFrontCoverChanged.connect(self.front_cover_changed)
        self.page_list_editor.listOrderChanged.connect(self.page_list_order_changed)
        self.tabWidget.currentChanged.connect(self.tab_changed)

        self.update_style_tweaks()

        self.show()
        self.set_app_position()
        if self.settings.last_form_side_width != -1:
            self.splitter.setSizes([self.settings.last_form_side_width, self.settings.last_list_side_width])
        self.raise_()
        QtCore.QCoreApplication.processEvents()
        self.resizeEvent(None)

        self.splitter.splitterMoved.connect(self.splitter_moved_event)

        self.fileSelectionList.add_app_action(self.actionAutoIdentify)
        self.fileSelectionList.add_app_action(self.actionAutoTag)
        self.fileSelectionList.add_app_action(self.actionCopyTags)
        self.fileSelectionList.add_app_action(self.actionRename)
        self.fileSelectionList.add_app_action(self.actionRemoveAuto)
        self.fileSelectionList.add_app_action(self.actionRepackage)

        if len(file_list) != 0:
            self.fileSelectionList.add_path_list(file_list)

        if self.settings.show_disclaimer:
            checked = OptionalMessageDialog.msg(
                self,
                "Welcome!",
                """
Thanks for trying ComicTagger!<br><br>
Be aware that this is beta-level software, and consider it experimental.
You should use it very carefully when modifying your data files.  As the
license says, it's "AS IS!"<br><br>
Also, be aware that writing tags to comic archives will change their file hashes,
which has implications with respect to other software packages.  It's best to
use ComicTagger on local copies of your comics.<br><br>
Have fun!
""",
            )
            self.settings.show_disclaimer = not checked

        if self.settings.check_for_new_version:
            # self.checkLatestVersionOnline()
            pass

    def sigint_handler(self, *args):
        # defer the actual close in the app loop thread
        QtCore.QTimer.singleShot(200, lambda: execute(self.close))

    def reset_app(self):

        self.archiveCoverWidget.clear()
        self.comic_archive: Optional[ComicArchive] = None
        self.dirty_flag = False
        self.clear_form()
        self.page_list_editor.reset_page()
        if self.page_browser is not None:
            self.page_browser.reset()
        self.update_app_title()
        self.update_menus()
        self.update_info_box()

        self.droppedFile = None
        self.page_loader = None

    def update_app_title(self):

        self.setWindowIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("app.png")))

        if self.comic_archive is None:
            self.setWindowTitle(self.appName)
        else:
            mod_str = ""
            ro_str = ""

            if self.dirty_flag:
                mod_str = " [modified]"

            if not self.comic_archive.is_writable():
                ro_str = " [read only]"

            self.setWindowTitle(self.appName + " - " + self.comic_archive.path + mod_str + ro_str)

    def config_menus(self):

        # File Menu
        self.actionExit.setShortcut("Ctrl+Q")
        self.actionExit.setStatusTip("Exit application")
        self.actionExit.triggered.connect(self.close)

        self.actionLoad.setShortcut("Ctrl+O")
        self.actionLoad.setStatusTip("Load comic archive")
        self.actionLoad.triggered.connect(self.select_file)

        self.actionLoadFolder.setShortcut("Ctrl+Shift+O")
        self.actionLoadFolder.setStatusTip("Load folder with comic archives")
        self.actionLoadFolder.triggered.connect(self.select_folder)

        self.actionWrite_Tags.setShortcut("Ctrl+S")
        self.actionWrite_Tags.setStatusTip("Save tags to comic archive")
        self.actionWrite_Tags.triggered.connect(self.commit_metadata)

        self.actionAutoTag.setShortcut("Ctrl+T")
        self.actionAutoTag.setStatusTip("Auto-tag multiple archives")
        self.actionAutoTag.triggered.connect(self.auto_tag)

        self.actionCopyTags.setShortcut("Ctrl+C")
        self.actionCopyTags.setStatusTip("Copy one tag style to another")
        self.actionCopyTags.triggered.connect(self.copy_tags)

        self.actionRemoveAuto.setShortcut("Ctrl+D")
        self.actionRemoveAuto.setStatusTip("Remove currently selected modify tag style from the archive")
        self.actionRemoveAuto.triggered.connect(self.remove_auto)

        self.actionRemoveCBLTags.setStatusTip("Remove ComicBookLover tags from comic archive")
        self.actionRemoveCBLTags.triggered.connect(self.remove_cbl_tags)

        self.actionRemoveCRTags.setStatusTip("Remove ComicRack tags from comic archive")
        self.actionRemoveCRTags.triggered.connect(self.remove_cr_tags)

        self.actionViewRawCRTags.setStatusTip("View raw ComicRack tag block from file")
        self.actionViewRawCRTags.triggered.connect(self.view_raw_cr_tags)

        self.actionViewRawCBLTags.setStatusTip("View raw ComicBookLover tag block from file")
        self.actionViewRawCBLTags.triggered.connect(self.view_raw_cbl_tags)

        self.actionRepackage.setShortcut("Ctrl+E")
        self.actionRepackage.setStatusTip("Re-create archive as CBZ")
        self.actionRepackage.triggered.connect(self.repackage_archive)

        self.actionRename.setShortcut("Ctrl+N")
        self.actionRename.setStatusTip("Rename archive based on tags")
        self.actionRename.triggered.connect(self.rename_archive)

        self.actionSettings.setShortcut("Ctrl+Shift+S")
        self.actionSettings.setStatusTip("Configure ComicTagger")
        self.actionSettings.triggered.connect(self.show_settings)

        # Tag Menu
        self.actionParse_Filename.setShortcut("Ctrl+F")
        self.actionParse_Filename.setStatusTip("Try to extract tags from filename")
        self.actionParse_Filename.triggered.connect(self.use_filename)

        self.actionSearchOnline.setShortcut("Ctrl+W")
        self.actionSearchOnline.setStatusTip("Search online for tags")
        self.actionSearchOnline.triggered.connect(self.query_online)

        self.actionAutoIdentify.setShortcut("Ctrl+I")
        self.actionAutoIdentify.triggered.connect(self.auto_identify_search)

        self.actionApplyCBLTransform.setShortcut("Ctrl+L")
        self.actionApplyCBLTransform.setStatusTip("Modify tags specifically for CBL format")
        self.actionApplyCBLTransform.triggered.connect(self.apply_cbl_transform)

        self.actionClearEntryForm.setShortcut("Ctrl+Shift+C")
        self.actionClearEntryForm.setStatusTip("Clear all the data on the screen")
        self.actionClearEntryForm.triggered.connect(self.clear_form)

        # Window Menu
        self.actionPageBrowser.setShortcut("Ctrl+P")
        self.actionPageBrowser.setStatusTip("Show the page browser")
        self.actionPageBrowser.triggered.connect(self.show_page_browser)

        # Help Menu
        self.actionAbout.setStatusTip("Show the " + self.appName + " info")
        self.actionAbout.triggered.connect(self.about_app)
        self.actionWiki.triggered.connect(self.show_wiki)
        self.actionReportBug.triggered.connect(self.report_bug)
        self.actionComicTaggerForum.triggered.connect(self.show_forum)

        # Notes Menu
        self.btnOpenWebLink.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("open.png")))

        # ToolBar
        self.actionLoad.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("open.png")))
        self.actionLoadFolder.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("longbox.png")))
        self.actionWrite_Tags.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("save.png")))
        self.actionParse_Filename.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("parse.png")))
        self.actionSearchOnline.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("search.png")))
        self.actionAutoIdentify.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("auto.png")))
        self.actionAutoTag.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("autotag.png")))
        self.actionClearEntryForm.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("clear.png")))
        self.actionPageBrowser.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("browse.png")))

        self.toolBar.addAction(self.actionLoad)
        self.toolBar.addAction(self.actionLoadFolder)
        self.toolBar.addAction(self.actionWrite_Tags)
        self.toolBar.addAction(self.actionSearchOnline)
        self.toolBar.addAction(self.actionAutoIdentify)
        self.toolBar.addAction(self.actionAutoTag)
        self.toolBar.addAction(self.actionClearEntryForm)
        self.toolBar.addAction(self.actionPageBrowser)

    def repackage_archive(self):
        ca_list = self.fileSelectionList.get_selected_archive_list()
        rar_count = 0
        for ca in ca_list:
            if ca.is_rar():
                rar_count += 1

        if rar_count == 0:
            QtWidgets.QMessageBox.information(
                self, self.tr("Export as Zip Archive"), self.tr("No RAR archives selected!")
            )
            logger.warning("Export as Zip Archive. No RAR archives selected")
            return

        if not self.dirty_flag_verification(
            "Export as Zip Archive",
            "If you export archives as Zip now, unsaved data in the form may be lost.  Are you sure?",
        ):
            return

        if rar_count != 0:
            dlg = ExportWindow(
                self,
                self.settings,
                f"""You have selected {rar_count} archive(s) to export  to Zip format.  New archives will be created in the same folder as the original.

Please choose options below, and select OK.
""",
            )
            dlg.adjustSize()
            dlg.setModal(True)
            if not dlg.exec():
                return

            prog_dialog = QtWidgets.QProgressDialog("", "Cancel", 0, rar_count, self)
            prog_dialog.setWindowTitle("Exporting as ZIP")
            prog_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
            prog_dialog.setMinimumDuration(300)
            center_window_on_parent(prog_dialog)
            QtCore.QCoreApplication.processEvents()
            prog_idx = 0

            new_archives_to_add = []
            archives_to_remove = []
            skipped_list = []
            failed_list = []
            success_count = 0

            for ca in ca_list:
                if ca.is_rar():
                    QtCore.QCoreApplication.processEvents()
                    if prog_dialog.wasCanceled():
                        break
                    prog_idx += 1
                    prog_dialog.setValue(prog_idx)
                    prog_dialog.setLabelText(ca.path)
                    center_window_on_parent(prog_dialog)
                    QtCore.QCoreApplication.processEvents()

                    original_path = os.path.abspath(ca.path)
                    export_name = os.path.splitext(original_path)[0] + ".cbz"

                    if os.path.lexists(export_name):
                        if dlg.fileConflictBehavior == ExportConflictOpts.dontCreate:
                            export_name = None
                            skipped_list.append(ca.path)
                        elif dlg.fileConflictBehavior == ExportConflictOpts.createUnique:
                            export_name = utils.unique_file(export_name)

                    if export_name is not None:
                        if ca.export_as_zip(export_name):
                            success_count += 1
                            if dlg.addToList:
                                new_archives_to_add.append(export_name)
                            if dlg.deleteOriginal:
                                archives_to_remove.append(ca)
                                os.unlink(ca.path)

                        else:
                            # last export failed, so remove the zip, if it
                            # exists
                            failed_list.append(ca.path)
                            if os.path.lexists(export_name):
                                os.remove(export_name)

            prog_dialog.hide()
            QtCore.QCoreApplication.processEvents()
            self.fileSelectionList.add_path_list(new_archives_to_add)
            self.fileSelectionList.remove_archive_list(archives_to_remove)

            summary = f"Successfully created {success_count} Zip archive(s)."
            if len(skipped_list) > 0:
                summary += (
                    f"\n\nThe following {len(skipped_list)} RAR archive(s) were skipped due to file name conflicts:\n"
                )
                for f in skipped_list:
                    summary += f"\t{f}\n"
            if len(failed_list) > 0:
                summary += (
                    f"\n\nThe following {len(failed_list)} RAR archive(s) failed to export due to read/write errors:\n"
                )
                for f in failed_list:
                    summary += f"\t{f}\n"

            logger.info(summary)
            dlg = LogWindow(self)
            dlg.set_text(summary)
            dlg.setWindowTitle("Archive Export to Zip Summary")
            dlg.exec()

    def about_app(self):

        website = "https://github.com/comictagger/comictagger"
        email = "comictagger@gmail.com"
        license_link = "http://www.apache.org/licenses/LICENSE-2.0"
        license_name = "Apache License 2.0"

        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle(("About " + self.appName))
        msg_box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        msg_box.setIconPixmap(QtGui.QPixmap(ComicTaggerSettings.get_graphic("about.png")))
        msg_box.setText(
            "<br><br><br>"
            + self.appName
            + f" v{self.version}"
            + "<br>"
            + "&copy;2014-2022 ComicTagger Devs<br><br>"
            + f"<a href='{website}'>{website}</a><br><br>"
            + f"<a href='mailto:{email}'>{email}</a><br><br>"
            + f"License: <a href='{license_link}'>{license_name}</a>"
        )

        msg_box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        msg_box.exec()

    def dragEnterEvent(self, event):
        self.droppedFiles = []
        if event.mimeData().hasUrls():

            # walk through the URL list and build a file list
            for url in event.mimeData().urls():
                if url.isValid() and url.scheme() == "file":
                    self.droppedFiles.append(url.toLocalFile())

            if self.droppedFiles is not None:
                event.accept()

    def dropEvent(self, event):
        self.fileSelectionList.add_path_list(self.droppedFiles)
        event.accept()

    def actual_load_current_archive(self):
        if self.metadata.is_empty:
            self.metadata = self.comic_archive.metadata_from_filename(self.settings.parse_scan_info)
        if len(self.metadata.pages) == 0:
            self.metadata.set_default_page_list(self.comic_archive.get_number_of_pages())

        self.update_cover_image()

        if self.page_browser is not None:
            self.page_browser.set_comic_archive(self.comic_archive)
            self.page_browser.metadata = self.metadata

        self.metadata_to_form()
        self.page_list_editor.set_data(self.comic_archive, self.metadata.pages)
        self.clear_dirty_flag()  # also updates the app title
        self.update_info_box()
        self.update_menus()
        self.update_app_title()

    def update_cover_image(self):
        cover_idx = self.metadata.get_cover_page_index_list()[0]
        self.archiveCoverWidget.set_archive(self.comic_archive, cover_idx)

    def update_menus(self):

        # First just disable all the questionable items
        self.actionAutoTag.setEnabled(False)
        self.actionCopyTags.setEnabled(False)
        self.actionRemoveAuto.setEnabled(False)
        self.actionRemoveCRTags.setEnabled(False)
        self.actionRemoveCBLTags.setEnabled(False)
        self.actionWrite_Tags.setEnabled(False)
        self.actionRepackage.setEnabled(False)
        self.actionViewRawCBLTags.setEnabled(False)
        self.actionViewRawCRTags.setEnabled(False)
        self.actionParse_Filename.setEnabled(False)
        self.actionAutoIdentify.setEnabled(False)
        self.actionRename.setEnabled(False)
        self.actionApplyCBLTransform.setEnabled(False)

        # now, selectively re-enable
        if self.comic_archive is not None:
            has_cix = self.comic_archive.has_cix()
            has_cbi = self.comic_archive.has_cbi()

            self.actionParse_Filename.setEnabled(True)
            self.actionAutoIdentify.setEnabled(True)
            self.actionAutoTag.setEnabled(True)
            self.actionRename.setEnabled(True)
            self.actionApplyCBLTransform.setEnabled(True)
            self.actionRepackage.setEnabled(True)
            self.actionRemoveAuto.setEnabled(True)
            self.actionRemoveCRTags.setEnabled(True)
            self.actionRemoveCBLTags.setEnabled(True)
            self.actionCopyTags.setEnabled(True)

            if has_cix:
                self.actionViewRawCRTags.setEnabled(True)
            if has_cbi:
                self.actionViewRawCBLTags.setEnabled(True)

            if self.comic_archive.is_writable():
                self.actionWrite_Tags.setEnabled(True)

    def update_info_box(self):

        ca = self.comic_archive

        if ca is None:
            self.lblFilename.setText("")
            self.lblArchiveType.setText("")
            self.lblTagList.setText("")
            self.lblPageCount.setText("")
            return

        filename = os.path.basename(ca.path)
        filename = os.path.splitext(filename)[0]
        filename = FileNameParser().fix_spaces(filename, False)

        self.lblFilename.setText(filename)

        if ca.is_sevenzip():
            self.lblArchiveType.setText("7Z archive")
        elif ca.is_zip():
            self.lblArchiveType.setText("ZIP archive")
        elif ca.is_rar():
            self.lblArchiveType.setText("RAR archive")
        elif ca.is_folder():
            self.lblArchiveType.setText("Folder archive")
        else:
            self.lblArchiveType.setText("")

        page_count = f" ({ca.get_number_of_pages()} pages)"
        self.lblPageCount.setText(page_count)

        tag_info = ""
        if ca.has_cix():
            tag_info = "• ComicRack tags"
        if ca.has_cbi():
            if tag_info != "":
                tag_info += "\n"
            tag_info += "• ComicBookLover tags"

        self.lblTagList.setText(tag_info)

    def set_dirty_flag(self):
        if not self.dirty_flag:
            self.dirty_flag = True
            self.fileSelectionList.set_modified_flag(True)
            self.update_app_title()

    def clear_dirty_flag(self):
        if self.dirty_flag:
            self.dirty_flag = False
            self.fileSelectionList.set_modified_flag(False)
            self.update_app_title()

    def connect_dirty_flag_signals(self):
        # recursively connect the tab form child slots
        self.connect_child_dirty_flag_signals(self.tabWidget)

    def connect_child_dirty_flag_signals(self, widget):

        if isinstance(widget, QtWidgets.QLineEdit):
            widget.textEdited.connect(self.set_dirty_flag)
        if isinstance(widget, QtWidgets.QTextEdit):
            widget.textChanged.connect(self.set_dirty_flag)
        if isinstance(widget, QtWidgets.QComboBox):
            widget.currentIndexChanged.connect(self.set_dirty_flag)
        if isinstance(widget, QtWidgets.QCheckBox):
            widget.stateChanged.connect(self.set_dirty_flag)

        # recursive call on children
        for child in widget.children():
            if child != self.page_list_editor:
                self.connect_child_dirty_flag_signals(child)

    def clear_form(self):
        # get a minty fresh metadata object
        self.metadata = GenericMetadata()
        if self.comic_archive is not None:
            self.metadata.set_default_page_list(self.comic_archive.get_number_of_pages())

        # recursively clear the tab form
        self.clear_children(self.tabWidget)

        # clear the dirty flag, since there is nothing in there now to lose
        self.clear_dirty_flag()

        self.page_list_editor.set_data(self.comic_archive, self.metadata.pages)

    def clear_children(self, widget):

        if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QTextEdit)):
            widget.setText("")
        if isinstance(widget, QtWidgets.QComboBox):
            widget.setCurrentIndex(0)
        if isinstance(widget, QtWidgets.QCheckBox):
            widget.setChecked(False)
        if isinstance(widget, QtWidgets.QTableWidget):
            while widget.rowCount() > 0:
                widget.removeRow(0)

        # recursive call on children
        for child in widget.children():
            self.clear_children(child)

    # Copy all of the metadata object into the form.
    # Merging of metadata should be done via the overlay function
    def metadata_to_form(self):
        def assign_text(field: Union[QtWidgets.QLineEdit, QtWidgets.QTextEdit], value):
            if value is not None:
                field.setText(str(value))

        md = self.metadata

        assign_text(self.leSeries, md.series)
        assign_text(self.leIssueNum, md.issue)
        assign_text(self.leIssueCount, md.issue_count)
        assign_text(self.leVolumeNum, md.volume)
        assign_text(self.leVolumeCount, md.volume_count)
        assign_text(self.leTitle, md.title)
        assign_text(self.lePublisher, md.publisher)
        assign_text(self.lePubMonth, md.month)
        assign_text(self.lePubYear, md.year)
        assign_text(self.lePubDay, md.day)
        assign_text(self.leGenre, md.genre)
        assign_text(self.leImprint, md.imprint)
        assign_text(self.teComments, md.comments)
        assign_text(self.teNotes, md.notes)
        assign_text(self.leCriticalRating, md.critical_rating)
        assign_text(self.leStoryArc, md.story_arc)
        assign_text(self.leScanInfo, md.scan_info)
        assign_text(self.leSeriesGroup, md.series_group)
        assign_text(self.leAltSeries, md.alternate_series)
        assign_text(self.leAltIssueNum, md.alternate_number)
        assign_text(self.leAltIssueCount, md.alternate_count)
        assign_text(self.leWebLink, md.web_link)
        assign_text(self.teCharacters, md.characters)
        assign_text(self.teTeams, md.teams)
        assign_text(self.teLocations, md.locations)

        if md.format is not None and md.format != "":
            i = self.cbFormat.findText(md.format)
            if i == -1:
                self.cbFormat.setEditText(md.format)
            else:
                self.cbFormat.setCurrentIndex(i)

        if md.maturity_rating is not None and md.maturity_rating != "":
            i = self.cbMaturityRating.findText(md.maturity_rating)
            if i == -1:
                self.cbMaturityRating.setEditText(md.maturity_rating)
            else:
                self.cbMaturityRating.setCurrentIndex(i)
        else:
            self.cbMaturityRating.setCurrentIndex(0)

        if md.language is not None:
            i = self.cbLanguage.findData(md.language)
            self.cbLanguage.setCurrentIndex(i)
        else:
            self.cbLanguage.setCurrentIndex(0)

        if md.country is not None:
            i = self.cbCountry.findText(md.country)
            self.cbCountry.setCurrentIndex(i)
        else:
            self.cbCountry.setCurrentIndex(0)

        if md.manga is not None:
            i = self.cbManga.findData(md.manga)
            self.cbManga.setCurrentIndex(i)
        else:
            self.cbManga.setCurrentIndex(0)

        if md.black_and_white:
            self.cbBW.setChecked(True)
        else:
            self.cbBW.setChecked(False)

        self.teTags.setText(utils.list_to_string(md.tags))

        while self.twCredits.rowCount() > 0:
            self.twCredits.removeRow(0)

        if md.credits is not None and len(md.credits) != 0:
            self.twCredits.setSortingEnabled(False)

            row = 0
            for credit in md.credits:
                # if the role-person pair already exists, just skip adding it to the list
                if self.is_dupe_credit(credit["role"].title(), credit["person"]):
                    continue

                self.add_new_credit_entry(
                    row, credit["role"].title(), credit["person"], (credit["primary"] if "primary" in credit else False)
                )

                row += 1

        self.twCredits.setSortingEnabled(True)
        self.update_credit_colors()

    def add_new_credit_entry(self, row, role, name, primary_flag=False):
        self.twCredits.insertRow(row)

        item_text = role
        item = QtWidgets.QTableWidgetItem(item_text)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        item.setData(QtCore.Qt.ItemDataRole.ToolTipRole, item_text)
        self.twCredits.setItem(row, 1, item)

        item_text = name
        item = QtWidgets.QTableWidgetItem(item_text)
        item.setData(QtCore.Qt.ItemDataRole.ToolTipRole, item_text)
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        self.twCredits.setItem(row, 2, item)

        item = QtWidgets.QTableWidgetItem("")
        item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
        self.twCredits.setItem(row, 0, item)
        self.update_credit_primary_flag(row, primary_flag)

    def is_dupe_credit(self, role, name):
        r = 0
        while r < self.twCredits.rowCount():
            if self.twCredits.item(r, 1).text() == role and self.twCredits.item(r, 2).text() == name:
                return True
            r = r + 1

        return False

    def form_to_metadata(self):
        # copy the data from the form into the metadata
        md = GenericMetadata()
        md.is_empty = False
        md.alternate_number = IssueString(self.leAltIssueNum.text()).as_string()
        md.issue = IssueString(self.leIssueNum.text()).as_string()
        md.issue_count = utils.xlate(self.leIssueCount.text(), True)
        md.volume = utils.xlate(self.leVolumeNum.text(), True)
        md.volume_count = utils.xlate(self.leVolumeCount.text(), True)
        md.month = utils.xlate(self.lePubMonth.text(), True)
        md.year = utils.xlate(self.lePubYear.text(), True)
        md.day = utils.xlate(self.lePubDay.text(), True)
        md.critical_rating = utils.xlate(self.leCriticalRating.text(), True)
        md.alternate_count = utils.xlate(self.leAltIssueCount.text(), True)

        md.series = self.leSeries.text()
        md.title = self.leTitle.text()
        md.publisher = self.lePublisher.text()
        md.genre = self.leGenre.text()
        md.imprint = self.leImprint.text()
        md.comments = self.teComments.toPlainText()
        md.notes = self.teNotes.toPlainText()
        md.maturity_rating = self.cbMaturityRating.currentText()

        md.story_arc = self.leStoryArc.text()
        md.scan_info = self.leScanInfo.text()
        md.series_group = self.leSeriesGroup.text()
        md.alternate_series = self.leAltSeries.text()
        md.web_link = self.leWebLink.text()
        md.characters = self.teCharacters.toPlainText()
        md.teams = self.teTeams.toPlainText()
        md.locations = self.teLocations.toPlainText()

        md.format = self.cbFormat.currentText()
        md.country = self.cbCountry.currentText()

        md.language = utils.xlate(self.cbLanguage.itemData(self.cbLanguage.currentIndex()))

        md.manga = utils.xlate(self.cbManga.itemData(self.cbManga.currentIndex()))

        # Make a list from the comma delimited tags string
        tmp = self.teTags.toPlainText()
        if tmp is not None:

            def strip_list(i):
                return [x.strip() for x in i]

            md.tags = strip_list(tmp.split(","))

        md.black_and_white = self.cbBW.isChecked()

        # get the credits from the table
        md.credits = []
        row = 0
        while row < self.twCredits.rowCount():
            role = self.twCredits.item(row, 1).text()
            name = self.twCredits.item(row, 2).text()
            primary_flag = self.twCredits.item(row, 0).text() != ""

            md.add_credit(name, role, bool(primary_flag))
            row += 1

        md.pages = self.page_list_editor.get_page_list()
        self.metadata = md

    def use_filename(self):
        if self.comic_archive is not None:
            # copy the form onto metadata object
            self.form_to_metadata()
            new_metadata = self.comic_archive.metadata_from_filename(self.settings.parse_scan_info)
            if new_metadata is not None:
                self.metadata.overlay(new_metadata)
                self.metadata_to_form()

    def select_folder(self):
        self.select_file(folder_mode=True)

    def select_file(self, folder_mode=False):

        dialog = QtWidgets.QFileDialog(self)
        if folder_mode:
            dialog.setFileMode(QtWidgets.QFileDialog.FileMode.Directory)
        else:
            dialog.setFileMode(QtWidgets.QFileDialog.FileMode.ExistingFiles)

        if self.settings.last_opened_folder is not None:
            dialog.setDirectory(self.settings.last_opened_folder)

        if not folder_mode:
            archive_filter = "Comic archive files (*.cbz *.zip *.cbr *.rar *.cb7 *.7z)"
            filters = [archive_filter, "Any files (*)"]
            dialog.setNameFilters(filters)

        if dialog.exec():
            file_list = dialog.selectedFiles()
            self.fileSelectionList.add_path_list(file_list)

    def auto_identify_search(self):
        if self.comic_archive is None:
            QtWidgets.QMessageBox.warning(self, "Automatic Identify Search", "You need to load a comic first!")
            return

        self.query_online(autoselect=True)

    def query_online(self, autoselect=False):

        issue_number = str(self.leIssueNum.text()).strip()

        if autoselect and issue_number == "":
            QtWidgets.QMessageBox.information(
                self, "Automatic Identify Search", "Can't auto-identify without an issue number (yet!)"
            )
            return

        if str(self.leSeries.text()).strip() != "":
            series_name = str(self.leSeries.text()).strip()
        else:
            QtWidgets.QMessageBox.information(self, "Online Search", "Need to enter a series name to search.")
            return

        year = str(self.lePubYear.text()).strip()
        if year == "":
            year = None

        issue_count = str(self.leIssueCount.text()).strip()
        if issue_count == "":
            issue_count = None

        cover_index_list = self.metadata.get_cover_page_index_list()
        selector = VolumeSelectionWindow(
            self,
            series_name,
            issue_number,
            year,
            issue_count,
            cover_index_list,
            self.comic_archive,
            self.settings,
            autoselect,
        )

        selector.setWindowTitle(f"Search: '{series_name}' - Select Series")

        selector.setModal(True)
        selector.exec()

        if selector.result():
            # we should now have a volume ID
            QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))

            # copy the form onto metadata object
            self.form_to_metadata()

            try:
                comic_vine = ComicVineTalker()
                new_metadata = comic_vine.fetch_issue_data(selector.volume_id, selector.issue_number, self.settings)
            except ComicVineTalkerException as e:
                QtWidgets.QApplication.restoreOverrideCursor()
                if e.code == ComicVineTalkerException.RateLimit:
                    QtWidgets.QMessageBox.critical(self, "Comic Vine Error", ComicVineTalker.get_rate_limit_message())
                else:
                    QtWidgets.QMessageBox.critical(
                        self, "Network Issue", "Could not connect to Comic Vine to get issue details.!"
                    )
            else:
                QtWidgets.QApplication.restoreOverrideCursor()
                if new_metadata is not None:
                    if self.settings.apply_cbl_transform_on_cv_import:
                        new_metadata = CBLTransformer(new_metadata, self.settings).apply()

                    if self.settings.clear_form_before_populating_from_cv:
                        self.clear_form()

                    self.metadata.overlay(new_metadata)
                    # Now push the new combined data into the edit controls
                    self.metadata_to_form()
                else:
                    QtWidgets.QMessageBox.critical(
                        self, "Search", f"Could not find an issue {selector.issue_number} for that series"
                    )

    def commit_metadata(self):
        if self.metadata is not None and self.comic_archive is not None:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Save Tags",
                f"Are you sure you wish to save {MetaDataStyle.name[self.save_data_style]} tags to this archive?",
                QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.No,
            )

            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
                self.form_to_metadata()

                success = self.comic_archive.write_metadata(self.metadata, self.save_data_style)
                self.comic_archive.load_cache([MetaDataStyle.CBI, MetaDataStyle.CIX])
                QtWidgets.QApplication.restoreOverrideCursor()

                if not success:
                    QtWidgets.QMessageBox.warning(self, "Save failed", "The tag save operation seemed to fail!")
                else:
                    self.clear_dirty_flag()
                    self.update_info_box()
                    self.update_menus()
                self.fileSelectionList.update_current_row()

        else:
            QtWidgets.QMessageBox.information(self, "Whoops!", "No data to commit!")

    def set_load_data_style(self, s):
        if self.dirty_flag_verification(
            "Change Tag Read Style", "If you change read tag style now, data in the form will be lost.  Are you sure?"
        ):
            self.load_data_style = self.cbLoadDataStyle.itemData(s)
            self.settings.last_selected_load_data_style = self.load_data_style
            self.update_menus()
            if self.comic_archive is not None:
                self.load_archive(self.comic_archive)
        else:
            self.cbLoadDataStyle.currentIndexChanged.disconnect(self.set_load_data_style)
            self.adjust_load_style_combo()
            self.cbLoadDataStyle.currentIndexChanged.connect(self.set_load_data_style)

    def set_save_data_style(self, s):
        self.save_data_style = self.cbSaveDataStyle.itemData(s)
        self.settings.last_selected_save_data_style = self.save_data_style
        self.update_style_tweaks()
        self.update_menus()

    def update_credit_colors(self):
        # !!!ATB qt5 porting TODO
        # return
        inactive_color = QtGui.QColor(255, 170, 150)
        active_palette = self.leSeries.palette()
        active_color = active_palette.color(QtGui.QPalette.ColorRole.Base)

        inactive_brush = QtGui.QBrush(inactive_color)
        active_brush = QtGui.QBrush(active_color)

        cix_credits = ComicInfoXml().get_parseable_credits()

        if self.save_data_style == MetaDataStyle.CIX:
            # loop over credit table, mark selected rows
            r = 0
            while r < self.twCredits.rowCount():
                if str(self.twCredits.item(r, 1).text()).lower() not in cix_credits:
                    self.twCredits.item(r, 1).setBackground(inactive_brush)
                else:
                    self.twCredits.item(r, 1).setBackground(active_brush)
                # turn off entire primary column
                self.twCredits.item(r, 0).setBackground(inactive_brush)
                r = r + 1

        if self.save_data_style == MetaDataStyle.CBI:
            # loop over credit table, make all active color
            r = 0
            while r < self.twCredits.rowCount():
                self.twCredits.item(r, 0).setBackground(active_brush)
                self.twCredits.item(r, 1).setBackground(active_brush)
                r = r + 1

    def update_style_tweaks(self):
        # depending on the current data style, certain fields are disabled

        inactive_color = QtGui.QColor(255, 170, 150)
        active_palette = self.leSeries.palette()

        inactive_palette1 = self.leSeries.palette()
        inactive_palette1.setColor(QtGui.QPalette.ColorRole.Base, inactive_color)

        inactive_palette2 = self.leSeries.palette()

        inactive_palette3 = self.leSeries.palette()
        inactive_palette3.setColor(QtGui.QPalette.ColorRole.Base, inactive_color)

        inactive_palette3.setColor(QtGui.QPalette.ColorRole.Base, inactive_color)

        # helper func
        def enable_widget(widget, enable):
            inactive_palette3.setColor(widget.backgroundRole(), inactive_color)
            inactive_palette2.setColor(widget.backgroundRole(), inactive_color)
            inactive_palette3.setColor(widget.foregroundRole(), inactive_color)

            if enable:
                widget.setPalette(active_palette)
                widget.setAutoFillBackground(False)
                if isinstance(widget, QtWidgets.QCheckBox):
                    widget.setEnabled(True)
                elif isinstance(widget, QtWidgets.QComboBox):
                    widget.setEnabled(True)
                else:
                    widget.setReadOnly(False)
            else:
                widget.setAutoFillBackground(True)
                if isinstance(widget, QtWidgets.QCheckBox):
                    widget.setPalette(inactive_palette2)
                    widget.setEnabled(False)
                elif isinstance(widget, QtWidgets.QComboBox):
                    widget.setPalette(inactive_palette3)
                    widget.setEnabled(False)
                else:
                    widget.setReadOnly(True)
                    widget.setPalette(inactive_palette1)

        cbi_only = [self.leVolumeCount, self.cbCountry, self.leCriticalRating, self.teTags]
        cix_only = [
            self.leImprint,
            self.teNotes,
            self.cbBW,
            self.cbManga,
            self.leStoryArc,
            self.leScanInfo,
            self.leSeriesGroup,
            self.leAltSeries,
            self.leAltIssueNum,
            self.leAltIssueCount,
            self.leWebLink,
            self.teCharacters,
            self.teTeams,
            self.teLocations,
            self.cbMaturityRating,
            self.cbFormat,
        ]

        if self.save_data_style == MetaDataStyle.CIX:
            for item in cix_only:
                enable_widget(item, True)
            for item in cbi_only:
                enable_widget(item, False)

        if self.save_data_style == MetaDataStyle.CBI:
            for item in cbi_only:
                enable_widget(item, True)
            for item in cix_only:
                enable_widget(item, False)

        self.update_credit_colors()
        self.page_list_editor.set_metadata_style(self.save_data_style)

    def cell_double_clicked(self, r, c):
        self.edit_credit()

    def add_credit(self):
        self.modify_credits("add")

    def edit_credit(self):
        if self.twCredits.currentRow() > -1:
            self.modify_credits("edit")

    def update_credit_primary_flag(self, row, primary):
        # if we're clearing a flag do it and quit
        if not primary:
            self.twCredits.item(row, 0).setText("")
            return

        # otherwise, we need to check for, and clear, other primaries with same role
        role = str(self.twCredits.item(row, 1).text())
        r = 0
        while r < self.twCredits.rowCount():
            if self.twCredits.item(r, 0).text() != "" and str(self.twCredits.item(r, 1).text()).lower() == role.lower():
                self.twCredits.item(r, 0).setText("")
            r = r + 1

        # Now set our new primary
        self.twCredits.item(row, 0).setText("Yes")

    def modify_credits(self, action):

        if action == "edit":
            row = self.twCredits.currentRow()
            role = self.twCredits.item(row, 1).text()
            name = self.twCredits.item(row, 2).text()
            primary = self.twCredits.item(row, 0).text() != ""
        else:
            row = self.twCredits.rowCount()
            role = ""
            name = ""
            primary = False

        editor = CreditEditorWindow(self, CreditEditorWindow.ModeEdit, role, name, primary)
        editor.setModal(True)
        editor.exec()
        if editor.result():
            new_role, new_name, new_primary = editor.get_credits()

            if new_name == name and new_role == role and new_primary == primary:
                # nothing has changed, just quit
                return

            # name and role is the same, but primary flag changed
            if new_name == name and new_role == role:
                self.update_credit_primary_flag(row, new_primary)
                return

            # check for dupes
            ok_to_mod = True
            if self.is_dupe_credit(new_role, new_name):
                # delete the dupe credit from list
                reply = QtWidgets.QMessageBox.question(
                    self,
                    "Duplicate Credit!",
                    "This will create a duplicate credit entry. Would you like to merge the entries, or create a duplicate?",
                    "Merge",
                    "Duplicate",
                )

                if reply == 0:
                    # merge
                    if action == "edit":
                        # just remove the row that would be same
                        self.twCredits.removeRow(row)
                        # TODO -- need to find the row of the dupe, and possible change the primary flag

                    ok_to_mod = False

            if ok_to_mod:
                # modify it
                if action == "edit":
                    self.twCredits.item(row, 1).setText(new_role)
                    self.twCredits.item(row, 2).setText(new_name)
                    self.update_credit_primary_flag(row, new_primary)
                else:
                    # add new entry
                    row = self.twCredits.rowCount()
                    self.add_new_credit_entry(row, new_role, new_name, new_primary)

            self.update_credit_colors()
            self.set_dirty_flag()

    def remove_credit(self):
        row = self.twCredits.currentRow()
        if row != -1:
            self.twCredits.removeRow(row)
        self.set_dirty_flag()

    def open_web_link(self):
        if self.leWebLink is not None:
            web_link = self.leWebLink.text().strip()
            valid = False
            try:
                result = urlparse(web_link)
                valid = all([result.scheme in ["http", "https"], result.netloc])
            except:
                pass

            if valid:
                webbrowser.open_new_tab(web_link)
            else:
                QtWidgets.QMessageBox.warning(self, self.tr("Web Link"), self.tr("Web Link is invalid."))

    def show_settings(self):

        settingswin = SettingsWindow(self, self.settings)
        settingswin.setModal(True)
        settingswin.exec()
        if settingswin.result():
            pass

    def set_app_position(self):
        if self.settings.last_main_window_width != 0:
            self.move(self.settings.last_main_window_x, self.settings.last_main_window_y)
            self.resize(self.settings.last_main_window_width, self.settings.last_main_window_height)
        else:
            screen = QtGui.QGuiApplication.primaryScreen().geometry()
            size = self.frameGeometry()
            self.move(int((screen.width() - size.width()) / 2), int((screen.height() - size.height()) / 2))

    def adjust_load_style_combo(self):
        # select the current style
        if self.load_data_style == MetaDataStyle.CBI:
            self.cbLoadDataStyle.setCurrentIndex(0)
        elif self.load_data_style == MetaDataStyle.CIX:
            self.cbLoadDataStyle.setCurrentIndex(1)

    def adjust_save_style_combo(self):
        # select the current style
        if self.save_data_style == MetaDataStyle.CBI:
            self.cbSaveDataStyle.setCurrentIndex(0)
        elif self.save_data_style == MetaDataStyle.CIX:
            self.cbSaveDataStyle.setCurrentIndex(1)
        self.update_style_tweaks()

    def populate_combo_boxes(self):

        # Add the entries to the tag style combobox
        self.cbLoadDataStyle.addItem("ComicBookLover", MetaDataStyle.CBI)
        self.cbLoadDataStyle.addItem("ComicRack", MetaDataStyle.CIX)
        self.adjust_load_style_combo()

        self.cbSaveDataStyle.addItem("ComicBookLover", MetaDataStyle.CBI)
        self.cbSaveDataStyle.addItem("ComicRack", MetaDataStyle.CIX)
        self.adjust_save_style_combo()

        # Add the entries to the country combobox
        self.cbCountry.addItem("", "")
        for f in natsort.humansorted(utils.countries.items(), operator.itemgetter(1)):
            self.cbCountry.addItem(f[1], f[0])

        # Add the entries to the language combobox
        self.cbLanguage.addItem("", "")

        for f in natsort.humansorted(utils.languages.items(), operator.itemgetter(1)):
            self.cbLanguage.addItem(f[1], f[0])

        # Add the entries to the manga combobox
        self.cbManga.addItem("", "")
        self.cbManga.addItem("Yes", "Yes")
        self.cbManga.addItem("Yes (Right to Left)", "YesAndRightToLeft")
        self.cbManga.addItem("No", "No")

        # Add the entries to the maturity combobox
        self.cbMaturityRating.addItem("", "")
        self.cbMaturityRating.addItem("Everyone", "")
        self.cbMaturityRating.addItem("G", "")
        self.cbMaturityRating.addItem("Early Childhood", "")
        self.cbMaturityRating.addItem("Everyone 10+", "")
        self.cbMaturityRating.addItem("PG", "")
        self.cbMaturityRating.addItem("Kids to Adults", "")
        self.cbMaturityRating.addItem("Teen", "")
        self.cbMaturityRating.addItem("MA15+", "")
        self.cbMaturityRating.addItem("Mature 17+", "")
        self.cbMaturityRating.addItem("R18+", "")
        self.cbMaturityRating.addItem("X18+", "")
        self.cbMaturityRating.addItem("Adults Only 18+", "")
        self.cbMaturityRating.addItem("Rating Pending", "")

        # Add entries to the format combobox
        self.cbFormat.addItem("")
        self.cbFormat.addItem(".1")
        self.cbFormat.addItem("-1")
        self.cbFormat.addItem("1 Shot")
        self.cbFormat.addItem("1/2")
        self.cbFormat.addItem("1-Shot")
        self.cbFormat.addItem("Annotation")
        self.cbFormat.addItem("Annotations")
        self.cbFormat.addItem("Annual")
        self.cbFormat.addItem("Anthology")
        self.cbFormat.addItem("B&W")
        self.cbFormat.addItem("B/W")
        self.cbFormat.addItem("B&&W")
        self.cbFormat.addItem("Black & White")
        self.cbFormat.addItem("Box Set")
        self.cbFormat.addItem("Box-Set")
        self.cbFormat.addItem("Crossover")
        self.cbFormat.addItem("Director's Cut")
        self.cbFormat.addItem("Epilogue")
        self.cbFormat.addItem("Event")
        self.cbFormat.addItem("FCBD")
        self.cbFormat.addItem("Flyer")
        self.cbFormat.addItem("Giant")
        self.cbFormat.addItem("Giant Size")
        self.cbFormat.addItem("Giant-Size")
        self.cbFormat.addItem("Graphic Novel")
        self.cbFormat.addItem("Hardcover")
        self.cbFormat.addItem("Hard-Cover")
        self.cbFormat.addItem("King")
        self.cbFormat.addItem("King Size")
        self.cbFormat.addItem("King-Size")
        self.cbFormat.addItem("Limited Series")
        self.cbFormat.addItem("Magazine")
        self.cbFormat.addItem("-1")
        self.cbFormat.addItem("NSFW")
        self.cbFormat.addItem("One Shot")
        self.cbFormat.addItem("One-Shot")
        self.cbFormat.addItem("Point 1")
        self.cbFormat.addItem("Preview")
        self.cbFormat.addItem("Prologue")
        self.cbFormat.addItem("Reference")
        self.cbFormat.addItem("Review")
        self.cbFormat.addItem("Reviewed")
        self.cbFormat.addItem("Scanlation")
        self.cbFormat.addItem("Script")
        self.cbFormat.addItem("Series")
        self.cbFormat.addItem("Sketch")
        self.cbFormat.addItem("Special")
        self.cbFormat.addItem("TPB")
        self.cbFormat.addItem("Trade Paper Back")
        self.cbFormat.addItem("WebComic")
        self.cbFormat.addItem("Web Comic")
        self.cbFormat.addItem("Year 1")
        self.cbFormat.addItem("Year One")

    def remove_auto(self):
        self.remove_tags(self.save_data_style)

    def remove_cbl_tags(self):
        self.remove_tags(MetaDataStyle.CBI)

    def remove_cr_tags(self):
        self.remove_tags(MetaDataStyle.CIX)

    def remove_tags(self, style):
        # remove the indicated tags from the archive
        ca_list = self.fileSelectionList.get_selected_archive_list()
        has_md_count = 0
        for ca in ca_list:
            if ca.has_metadata(style):
                has_md_count += 1

        if has_md_count == 0:
            QtWidgets.QMessageBox.information(
                self, "Remove Tags", f"No archives with {MetaDataStyle.name[style]} tags selected!"
            )
            return

        if has_md_count != 0 and not self.dirty_flag_verification(
            "Remove Tags", "If you remove tags now, unsaved data in the form will be lost.  Are you sure?"
        ):
            return

        if has_md_count != 0:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Remove Tags",
                f"Are you sure you wish to remove the {MetaDataStyle.name[style]} tags from {has_md_count} archive(s)?",
                QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.No,
            )

            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                progdialog = QtWidgets.QProgressDialog("", "Cancel", 0, has_md_count, self)
                progdialog.setWindowTitle("Removing Tags")
                progdialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
                progdialog.setMinimumDuration(300)
                center_window_on_parent(progdialog)
                QtCore.QCoreApplication.processEvents()

                prog_idx = 0

                failed_list = []
                success_count = 0
                for ca in ca_list:
                    if ca.has_metadata(style):
                        QtCore.QCoreApplication.processEvents()
                        if progdialog.wasCanceled():
                            break
                        prog_idx += 1
                        progdialog.setValue(prog_idx)
                        progdialog.setLabelText(ca.path)
                        center_window_on_parent(progdialog)
                        QtCore.QCoreApplication.processEvents()

                    if ca.has_metadata(style) and ca.is_writable():
                        if not ca.remove_metadata(style):
                            failed_list.append(ca.path)
                        else:
                            success_count += 1
                        ca.load_cache([MetaDataStyle.CBI, MetaDataStyle.CIX])

                progdialog.hide()
                QtCore.QCoreApplication.processEvents()
                self.fileSelectionList.update_selected_rows()
                self.update_info_box()
                self.update_menus()

                summary = f"Successfully removed tags in {success_count} archive(s)."
                if len(failed_list) > 0:
                    summary += f"\n\nThe remove operation failed in the following {len(failed_list)} archive(s):\n"
                    for f in failed_list:
                        summary += f"\t{f}\n"

                dlg = LogWindow(self)
                dlg.set_text(summary)
                dlg.setWindowTitle("Tag Remove Summary")
                dlg.exec()

    def copy_tags(self):
        # copy the indicated tags in the archive
        ca_list = self.fileSelectionList.get_selected_archive_list()
        has_src_count = 0

        src_style = self.load_data_style
        dest_style = self.save_data_style

        if src_style == dest_style:
            QtWidgets.QMessageBox.information(
                self, "Copy Tags", "Can't copy tag style onto itself.  Read style and modify style must be different."
            )
            return

        for ca in ca_list:
            if ca.has_metadata(src_style):
                has_src_count += 1

        if has_src_count == 0:
            QtWidgets.QMessageBox.information(self, "Copy Tags", f"No archives with {src_style} tags selected!")
            return

        if has_src_count != 0 and not self.dirty_flag_verification(
            "Copy Tags", "If you copy tags now, unsaved data in the form may be lost.  Are you sure?"
        ):
            return

        if has_src_count != 0:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Copy Tags",
                f"Are you sure you wish to copy the {MetaDataStyle.name[src_style]} tags to {MetaDataStyle.name[dest_style]} tags in {has_src_count} archive(s)?",
                QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.No,
            )

            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                prog_dialog = QtWidgets.QProgressDialog("", "Cancel", 0, has_src_count, self)
                prog_dialog.setWindowTitle("Copying Tags")
                prog_dialog.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
                prog_dialog.setMinimumDuration(300)
                center_window_on_parent(prog_dialog)
                QtCore.QCoreApplication.processEvents()
                prog_idx = 0

                failed_list = []
                success_count = 0
                for ca in ca_list:
                    if ca.has_metadata(src_style):
                        QtCore.QCoreApplication.processEvents()
                        if prog_dialog.wasCanceled():
                            break
                        prog_idx += 1
                        prog_dialog.setValue(prog_idx)
                        prog_dialog.setLabelText(ca.path)
                        center_window_on_parent(prog_dialog)
                        QtCore.QCoreApplication.processEvents()

                    if ca.has_metadata(src_style) and ca.is_writable():
                        md = ca.read_metadata(src_style)

                        if dest_style == MetaDataStyle.CBI and self.settings.apply_cbl_transform_on_bulk_operation:
                            md = CBLTransformer(md, self.settings).apply()

                        if not ca.write_metadata(md, dest_style):
                            failed_list.append(ca.path)
                        else:
                            success_count += 1

                        ca.load_cache([MetaDataStyle.CBI, MetaDataStyle.CIX])

                prog_dialog.hide()
                QtCore.QCoreApplication.processEvents()
                self.fileSelectionList.update_selected_rows()
                self.update_info_box()
                self.update_menus()

                summary = f"Successfully copied tags in {success_count} archive(s)."
                if len(failed_list) > 0:
                    summary += f"\n\nThe copy operation failed in the following {len(failed_list)} archive(s):\n"
                    for f in failed_list:
                        summary += f"\t{f}\n"

                dlg = LogWindow(self)
                dlg.set_text(summary)
                dlg.setWindowTitle("Tag Copy Summary")
                dlg.exec()

    def actual_issue_data_fetch(self, match):

        # now get the particular issue data
        cv_md = None
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))

        try:
            comic_vine = ComicVineTalker()
            comic_vine.wait_for_rate_limit = self.settings.wait_and_retry_on_rate_limit
            cv_md = comic_vine.fetch_issue_data(match["volume_id"], match["issue_number"], self.settings)
        except ComicVineTalkerException:
            logger.exception("Network error while getting issue details. Save aborted")

        if cv_md is not None:
            if self.settings.apply_cbl_transform_on_cv_import:
                cv_md = CBLTransformer(cv_md, self.settings).apply()

        QtWidgets.QApplication.restoreOverrideCursor()

        return cv_md

    def auto_tag_log(self, text):
        IssueIdentifier.default_write_output(text)
        if self.atprogdialog is not None:
            self.atprogdialog.textEdit.append(text.rstrip())
            self.atprogdialog.textEdit.ensureCursorVisible()
            QtCore.QCoreApplication.processEvents()
            QtCore.QCoreApplication.processEvents()
            QtCore.QCoreApplication.processEvents()

    def identify_and_tag_single_archive(
        self, ca: ComicArchive, match_results: OnlineMatchResults, dlg: AutoTagStartWindow
    ):
        success = False
        ii = IssueIdentifier(ca, self.settings)

        # read in metadata, and parse file name if not there
        md = ca.read_metadata(self.save_data_style)
        if md.is_empty:
            md = ca.metadata_from_filename(self.settings.parse_scan_info)
            if dlg.ignore_leading_digits_in_filename and md.series is not None:
                # remove all leading numbers
                md.series = re.sub(r"([\d.]*)(.*)", "\\2", md.series)

        # use the dialog specified search string
        if dlg.search_string is not None:
            md.series = dlg.search_string

        if md is None or md.is_empty:
            logger.error("No metadata given to search online with!")
            return False, match_results

        if dlg.dont_use_year:
            md.year = None
        if dlg.assume_issue_one and (md.issue is None or md.issue == ""):
            md.issue = "1"
        ii.set_additional_metadata(md)
        ii.only_use_additional_meta_data = True
        ii.wait_and_retry_on_rate_limit = dlg.wait_and_retry_on_rate_limit
        ii.set_output_function(self.auto_tag_log)
        ii.cover_page_index = md.get_cover_page_index_list()[0]
        ii.set_cover_url_callback(self.atprogdialog.set_test_image)
        ii.set_name_length_delta_threshold(dlg.name_length_match_tolerance)

        matches: List[IssueResult] = ii.search()

        result = ii.search_result

        found_match = False
        choices = False
        low_confidence = False

        if result == ii.result_no_matches:
            pass
        elif result == ii.result_found_match_but_bad_cover_score:
            low_confidence = True
            found_match = True
        elif result == ii.result_found_match_but_not_first_page:
            found_match = True
        elif result == ii.result_multiple_matches_with_bad_image_scores:
            low_confidence = True
            choices = True
        elif result == ii.result_one_good_match:
            found_match = True
        elif result == ii.result_multiple_good_matches:
            choices = True

        if choices:
            if low_confidence:
                self.auto_tag_log("Online search: Multiple low-confidence matches.  Save aborted\n")
                match_results.low_confidence_matches.append(MultipleMatch(ca, matches))
            else:
                self.auto_tag_log("Online search: Multiple matches.  Save aborted\n")
                match_results.multiple_matches.append(MultipleMatch(ca, matches))
        elif low_confidence and not dlg.auto_save_on_low:
            self.auto_tag_log("Online search: Low confidence match.  Save aborted\n")
            match_results.low_confidence_matches.append(MultipleMatch(ca, matches))
        elif not found_match:
            self.auto_tag_log("Online search: No match found.  Save aborted\n")
            match_results.no_matches.append(ca.path)
        else:
            # a single match!
            if low_confidence:
                self.auto_tag_log("Online search: Low confidence match, but saving anyways, as indicated...\n")

            # now get the particular issue data
            cv_md = self.actual_issue_data_fetch(matches[0])
            if cv_md is None:
                match_results.fetch_data_failures.append(ca.path)

            if cv_md is not None:
                md.overlay(cv_md)

                if not ca.write_metadata(md, self.save_data_style):
                    match_results.write_failures.append(ca.path)
                    self.auto_tag_log("Save failed ;-(\n")
                else:
                    match_results.good_matches.append(ca.path)
                    success = True
                    self.auto_tag_log("Save complete!\n")
                ca.load_cache([MetaDataStyle.CBI, MetaDataStyle.CIX])

        return success, match_results

    def auto_tag(self):
        ca_list = self.fileSelectionList.get_selected_archive_list()
        style = self.save_data_style

        if len(ca_list) == 0:
            QtWidgets.QMessageBox.information(self, "Auto-Tag", "No archives selected!")
            return

        if not self.dirty_flag_verification(
            "Auto-Tag", "If you auto-tag now, unsaved data in the form will be lost.  Are you sure?"
        ):
            return

        atstartdlg = AutoTagStartWindow(
            self,
            self.settings,
            f"""You have selected {len(ca_list)} archive(s) to automatically identify and write {MetaDataStyle.name[style]} tags to.

Please choose options below, and select OK to Auto-Tag.
""",
        )

        atstartdlg.adjustSize()
        atstartdlg.setModal(True)
        if not atstartdlg.exec():
            return

        self.atprogdialog = AutoTagProgressWindow(self)
        self.atprogdialog.setModal(True)
        self.atprogdialog.show()
        self.atprogdialog.progressBar.setMaximum(len(ca_list))
        self.atprogdialog.setWindowTitle("Auto-Tagging")

        self.auto_tag_log("==========================================================================\n")
        self.auto_tag_log(f"Auto-Tagging Started for {len(ca_list)} items\n")

        prog_idx = 0

        match_results = OnlineMatchResults()
        archives_to_remove = []
        for ca in ca_list:
            self.auto_tag_log("==========================================================================\n")
            self.auto_tag_log(f"Auto-Tagging {prog_idx + 1} of {len(ca_list)}\n")
            self.auto_tag_log(f"{ca.path}\n")
            cover_idx = ca.read_metadata(style).get_cover_page_index_list()[0]
            image_data = ca.get_page(cover_idx)
            self.atprogdialog.set_archive_image(image_data)
            self.atprogdialog.set_test_image(None)

            QtCore.QCoreApplication.processEvents()
            if self.atprogdialog.isdone:
                break
            self.atprogdialog.progressBar.setValue(prog_idx)
            prog_idx += 1
            self.atprogdialog.label.setText(ca.path)
            center_window_on_parent(self.atprogdialog)
            QtCore.QCoreApplication.processEvents()

            if ca.is_writable():
                success, match_results = self.identify_and_tag_single_archive(ca, match_results, atstartdlg)

                if success and atstartdlg.remove_after_success:
                    archives_to_remove.append(ca)

        self.atprogdialog.close()

        if atstartdlg.remove_after_success:
            self.fileSelectionList.remove_archive_list(archives_to_remove)
        self.fileSelectionList.update_selected_rows()

        self.load_archive(self.fileSelectionList.get_current_archive())
        self.atprogdialog = None

        summary = ""
        summary += f"Successfully tagged archives: {len(match_results.good_matches)}\n"

        if len(match_results.multiple_matches) > 0:
            summary += f"Archives with multiple matches: {len(match_results.multiple_matches)}\n"
        if len(match_results.low_confidence_matches) > 0:
            summary += (
                f"Archives with one or more low-confidence matches: {len(match_results.low_confidence_matches)}\n"
            )
        if len(match_results.no_matches) > 0:
            summary += f"Archives with no matches: {len(match_results.no_matches)}\n"
        if len(match_results.fetch_data_failures) > 0:
            summary += f"Archives that failed due to data fetch errors: {len(match_results.fetch_data_failures)}\n"
        if len(match_results.write_failures) > 0:
            summary += f"Archives that failed due to file writing errors: {len(match_results.write_failures)}\n"

        self.auto_tag_log(summary)

        sum_selectable = len(match_results.multiple_matches) + len(match_results.low_confidence_matches)
        if sum_selectable > 0:
            summary += (
                "\n\nDo you want to manually select the ones with multiple matches and/or low-confidence matches now?"
            )

            reply = QtWidgets.QMessageBox.question(
                self,
                "Auto-Tag Summary",
                summary,
                QtWidgets.QMessageBox.StandardButton.Yes,
                QtWidgets.QMessageBox.StandardButton.No,
            )

            match_results.multiple_matches.extend(match_results.low_confidence_matches)
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                matchdlg = AutoTagMatchWindow(self, match_results.multiple_matches, style, self.actual_issue_data_fetch)
                matchdlg.setModal(True)
                matchdlg.exec()
                self.fileSelectionList.update_selected_rows()
                self.load_archive(self.fileSelectionList.get_current_archive())

        else:
            QtWidgets.QMessageBox.information(self, self.tr("Auto-Tag Summary"), self.tr(summary))
        logger.info(summary)

    def dirty_flag_verification(self, title, desc):
        if self.dirty_flag:
            reply = QtWidgets.QMessageBox.question(
                self,
                title,
                desc,
                (
                    QtWidgets.QMessageBox.StandardButton.Save
                    | QtWidgets.QMessageBox.StandardButton.Cancel
                    | QtWidgets.QMessageBox.StandardButton.Discard
                ),
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )

            if reply == QtWidgets.QMessageBox.StandardButton.Discard:
                return True
            if reply == QtWidgets.QMessageBox.StandardButton.Save:
                self.commit_metadata()
                return True
            return False
        return True

    def closeEvent(self, event):
        if self.dirty_flag_verification(
            f"Exit {self.appName}", "If you quit now, data in the form will be lost.  Are you sure?"
        ):
            appsize = self.size()
            self.settings.last_main_window_width = appsize.width()
            self.settings.last_main_window_height = appsize.height()
            self.settings.last_main_window_x = self.x()
            self.settings.last_main_window_y = self.y()
            self.settings.last_form_side_width = self.splitter.sizes()[0]
            self.settings.last_list_side_width = self.splitter.sizes()[1]
            (
                self.settings.last_filelist_sorted_column,
                self.settings.last_filelist_sorted_order,
            ) = self.fileSelectionList.get_sorting()
            self.settings.save()

            event.accept()
        else:
            event.ignore()

    def show_page_browser(self):
        if self.page_browser is None:
            self.page_browser = PageBrowserWindow(self, self.metadata)
            if self.comic_archive is not None:
                self.page_browser.set_comic_archive(self.comic_archive)
            self.page_browser.finished.connect(self.page_browser_closed)

    def page_browser_closed(self):
        self.page_browser = None

    def view_raw_cr_tags(self):
        if self.comic_archive is not None and self.comic_archive.has_cix():
            dlg = LogWindow(self)
            dlg.set_text(self.comic_archive.read_raw_cix())
            dlg.setWindowTitle("Raw ComicRack Tag View")
            dlg.exec()

    def view_raw_cbl_tags(self):
        if self.comic_archive is not None and self.comic_archive.has_cbi():
            dlg = LogWindow(self)
            text = pprint.pformat(json.loads(self.comic_archive.read_raw_cbi()), indent=4)
            dlg.set_text(text)
            dlg.setWindowTitle("Raw ComicBookLover Tag View")
            dlg.exec()

    def show_wiki(self):
        webbrowser.open("https://github.com/comictagger/comictagger/wiki")

    def report_bug(self):
        webbrowser.open("https://github.com/comictagger/comictagger/issues")

    def show_forum(self):
        webbrowser.open("http://comictagger.forumotion.com/")

    def front_cover_changed(self):
        self.metadata.pages = self.page_list_editor.get_page_list()
        self.update_cover_image()

    def page_list_order_changed(self):
        self.metadata.pages = self.page_list_editor.get_page_list()

    def apply_cbl_transform(self):
        self.form_to_metadata()
        self.metadata = CBLTransformer(self.metadata, self.settings).apply()
        self.metadata_to_form()

    def rename_archive(self):
        ca_list = self.fileSelectionList.get_selected_archive_list()

        if len(ca_list) == 0:
            QtWidgets.QMessageBox.information(self, "Rename", "No archives selected!")
            return

        if self.dirty_flag_verification(
            "File Rename", "If you rename files now, unsaved data in the form will be lost.  Are you sure?"
        ):

            dlg = RenameWindow(self, ca_list, self.load_data_style, self.settings)
            dlg.setModal(True)
            if dlg.exec():
                self.fileSelectionList.update_selected_rows()
                self.load_archive(self.comic_archive)

    def file_list_selection_changed(self, fi: FileInfo):
        self.load_archive(fi.ca)

    def load_archive(self, comic_archive: ComicArchive):
        self.comic_archive = None
        self.clear_form()

        if not os.path.exists(comic_archive.path):
            self.fileSelectionList.dirty_flag = False
            self.fileSelectionList.remove_archive_list([comic_archive])
            QtCore.QTimer.singleShot(1, self.fileSelectionList.revert_selection)
            return

        self.settings.last_opened_folder = os.path.abspath(os.path.split(comic_archive.path)[0])
        self.comic_archive = comic_archive
        self.metadata = self.comic_archive.read_metadata(self.load_data_style)
        if self.metadata is None:
            self.metadata = GenericMetadata()

        self.actual_load_current_archive()

    def file_list_cleared(self):
        self.reset_app()

    def splitter_moved_event(self, w1, w2):
        scrollbar_w = 0
        if self.scrollArea.verticalScrollBar().isVisible():
            scrollbar_w = self.scrollArea.verticalScrollBar().width()

        new_w = self.scrollArea.width() - scrollbar_w - 5
        self.scrollAreaWidgetContents.resize(new_w, self.scrollAreaWidgetContents.height())

    def resizeEvent(self, ev):
        self.splitter_moved_event(0, 0)

    def tab_changed(self, idx):
        if idx == 0:
            self.splitter_moved_event(0, 0)

    def check_latest_version_online(self):
        version_checker = VersionChecker()
        self.version_check_complete(
            version_checker.get_latest_version(self.settings.install_id, self.settings.send_usage_stats)
        )

    def version_check_complete(self, new_version):
        if new_version not in (self.version, self.settings.dont_notify_about_this_version):
            website = "https://github.com/comictagger/comictagger"
            checked = OptionalMessageDialog.msg(
                self,
                "New version available!",
                f"New version ({new_version}) available!<br>(You are currently running {self.version})<br><br>"
                f"Visit <a href='{website}'>{website}</a> for more info.<br><br>",
                QtCore.Qt.CheckState.Unchecked,
                "Don't tell me about this version again",
            )
            if checked:
                self.settings.dont_notify_about_this_version = new_version

    def on_incoming_socket_connection(self):
        # Accept connection from other instance.
        # Read in the file list if they're giving it, and add to our own list
        local_socket = self.socketServer.nextPendingConnection()
        if local_socket.waitForReadyRead(3000):
            byte_array = local_socket.readAll().data()
            if len(byte_array) > 0:
                obj = pickle.loads(byte_array)
                local_socket.disconnectFromServer()
                if isinstance(obj, list):
                    self.fileSelectionList.add_path_list(obj)

        self.bring_to_top()

    def bring_to_top(self):
        if platform.system() == "Windows":
            self.showNormal()
            self.raise_()
            self.activateWindow()
            try:
                import win32con
                import win32gui

                hwnd = self.effectiveWinId()
                rect = win32gui.GetWindowRect(hwnd)
                x = rect[0]
                y = rect[1]
                w = rect[2] - x
                h = rect[3] - y
                # mark it "always on top", just for a moment, to force it to
                # the top
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, w, h, 0)
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, x, y, w, h, 0)
            except Exception as e:
                print("Whoops", e)
        elif platform.system() == "Darwin":
            self.raise_()
            self.showNormal()
            self.activateWindow()
        else:
            flags = QtCore.Qt.WindowType(self.windowFlags())
            self.setWindowFlags(
                flags | QtCore.Qt.WindowType.WindowStaysOnTopHint | QtCore.Qt.WindowType.X11BypassWindowManagerHint
            )
            QtCore.QCoreApplication.processEvents()
            self.setWindowFlags(flags)
            self.show()
