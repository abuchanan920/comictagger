"""A PyQT4 dialog to select specific issue from list"""

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


import logging

from PyQt5 import QtCore, QtGui, QtWidgets, uic

from comicapi.issuestring import IssueString
from comictaggerlib.comicvinetalker import ComicVineTalker, ComicVineTalkerException
from comictaggerlib.coverimagewidget import CoverImageWidget
from comictaggerlib.settings import ComicTaggerSettings
from comictaggerlib.ui.qtutils import reduce_widget_font_size

logger = logging.getLogger(__name__)


class IssueNumberTableWidgetItem(QtWidgets.QTableWidgetItem):
    def __lt__(self, other):
        self_str = self.data(QtCore.Qt.ItemDataRole.DisplayRole)
        other_str = other.data(QtCore.Qt.ItemDataRole.DisplayRole)
        return IssueString(self_str).as_float() < IssueString(other_str).as_float()


class IssueSelectionWindow(QtWidgets.QDialog):
    volume_id = 0

    def __init__(self, parent, settings, series_id, issue_number):
        super().__init__(parent)

        uic.loadUi(ComicTaggerSettings.get_ui_file("issueselectionwindow.ui"), self)

        self.coverWidget = CoverImageWidget(self.coverImageContainer, CoverImageWidget.AltCoverMode)
        gridlayout = QtWidgets.QGridLayout(self.coverImageContainer)
        gridlayout.addWidget(self.coverWidget)
        gridlayout.setContentsMargins(0, 0, 0, 0)

        reduce_widget_font_size(self.twList)
        reduce_widget_font_size(self.teDescription, 1)

        self.setWindowFlags(
            QtCore.Qt.WindowType(
                self.windowFlags()
                | QtCore.Qt.WindowType.WindowSystemMenuHint
                | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            )
        )

        self.series_id = series_id
        self.issue_id = None
        self.settings = settings
        self.url_fetch_thread = None
        self.issue_list = []

        if issue_number is None or issue_number == "":
            self.issue_number = 1
        else:
            self.issue_number = issue_number

        self.initial_id = None
        self.perform_query()

        self.twList.resizeColumnsToContents()
        self.twList.currentItemChanged.connect(self.current_item_changed)
        self.twList.cellDoubleClicked.connect(self.cell_double_clicked)

        # now that the list has been sorted, find the initial record, and
        # select it
        if self.initial_id is None:
            self.twList.selectRow(0)
        else:
            for r in range(0, self.twList.rowCount()):
                issue_id = self.twList.item(r, 0).data(QtCore.Qt.ItemDataRole.UserRole)
                if issue_id == self.initial_id:
                    self.twList.selectRow(r)
                    break

    def perform_query(self):

        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))

        try:
            comic_vine = ComicVineTalker()
            comic_vine.fetch_volume_data(self.series_id)
            self.issue_list = comic_vine.fetch_issues_by_volume(self.series_id)
        except ComicVineTalkerException as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            if e.code == ComicVineTalkerException.RateLimit:
                QtWidgets.QMessageBox.critical(self, "Comic Vine Error", ComicVineTalker.get_rate_limit_message())
            else:
                QtWidgets.QMessageBox.critical(self, "Network Issue", "Could not connect to Comic Vine to list issues!")
            return

        while self.twList.rowCount() > 0:
            self.twList.removeRow(0)

        self.twList.setSortingEnabled(False)

        row = 0
        for record in self.issue_list:
            self.twList.insertRow(row)

            item_text = record["issue_number"]
            item = IssueNumberTableWidgetItem(item_text)
            item.setData(QtCore.Qt.ItemDataRole.ToolTipRole, item_text)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, record["id"])
            item.setData(QtCore.Qt.ItemDataRole.DisplayRole, item_text)
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.twList.setItem(row, 0, item)

            item_text = record["cover_date"]
            if item_text is None:
                item_text = ""
            # remove the day of "YYYY-MM-DD"
            parts = item_text.split("-")
            if len(parts) > 1:
                item_text = parts[0] + "-" + parts[1]

            item = QtWidgets.QTableWidgetItem(item_text)
            item.setData(QtCore.Qt.ItemDataRole.ToolTipRole, item_text)
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.twList.setItem(row, 1, item)

            item_text = record["name"]
            if item_text is None:
                item_text = ""
            item = QtWidgets.QTableWidgetItem(item_text)
            item.setData(QtCore.Qt.ItemDataRole.ToolTipRole, item_text)
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.twList.setItem(row, 2, item)

            if (
                IssueString(record["issue_number"]).as_string().lower()
                == IssueString(self.issue_number).as_string().lower()
            ):
                self.initial_id = record["id"]

            row += 1

        self.twList.setSortingEnabled(True)
        self.twList.sortItems(0, QtCore.Qt.SortOrder.AscendingOrder)

        QtWidgets.QApplication.restoreOverrideCursor()

    def cell_double_clicked(self, r, c):
        self.accept()

    def current_item_changed(self, curr, prev):

        if curr is None:
            return
        if prev is not None and prev.row() == curr.row():
            return

        self.issue_id = self.twList.item(curr.row(), 0).data(QtCore.Qt.ItemDataRole.UserRole)

        # list selection was changed, update the the issue cover
        for record in self.issue_list:
            if record["id"] == self.issue_id:
                self.issue_number = record["issue_number"]
                self.coverWidget.set_issue_id(int(self.issue_id))
                if record["description"] is None:
                    self.teDescription.setText("")
                else:
                    self.teDescription.setText(record["description"])

                break
