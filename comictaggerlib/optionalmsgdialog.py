"""A PyQt5 dialog to show a message and let the user check a box

Example usage:

checked = OptionalMessageDialog.msg(self, "Disclaimer",
                            "This is beta software, and you are using it at your own risk!",
                         )

said_yes, checked = OptionalMessageDialog.question(self, "QtWidgets.Question",
                            "Are you sure you wish to do this?",
                         )
"""

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

from PyQt5 import QtCore, QtWidgets

logger = logging.getLogger(__name__)

StyleMessage = 0
StyleQuestion = 1


class OptionalMessageDialog(QtWidgets.QDialog):
    def __init__(self, parent, style, title, msg, check_state=QtCore.Qt.CheckState.Unchecked, check_text=None):
        super().__init__(parent)

        self.setWindowTitle(title)
        self.was_accepted = False
        layout = QtWidgets.QVBoxLayout(self)

        self.theLabel = QtWidgets.QLabel(msg)
        self.theLabel.setWordWrap(True)
        self.theLabel.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.theLabel.setOpenExternalLinks(True)
        self.theLabel.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            | QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse
            | QtCore.Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )

        layout.addWidget(self.theLabel)
        layout.insertSpacing(-1, 10)

        if check_text is None:
            if style == StyleQuestion:
                check_text = "Remember this answer"
            else:
                check_text = "Don't show this message again"

        self.theCheckBox = QtWidgets.QCheckBox(check_text)

        self.theCheckBox.setCheckState(check_state)

        layout.addWidget(self.theCheckBox)

        btnbox_style = QtWidgets.QDialogButtonBox.StandardButton.Ok
        if style == StyleQuestion:
            btnbox_style = QtWidgets.QDialogButtonBox.StandardButton.Yes | QtWidgets.QDialogButtonBox.StandardButton.No

        self.theButtonBox = QtWidgets.QDialogButtonBox(
            btnbox_style,
            parent=self,
            accepted=self.accept,
            rejected=self.reject,
        )

        layout.addWidget(self.theButtonBox)

    def accept(self):
        self.was_accepted = True
        QtWidgets.QDialog.accept(self)

    def reject(self):
        self.was_accepted = False
        QtWidgets.QDialog.reject(self)

    @staticmethod
    def msg(parent, title, msg, check_state=QtCore.Qt.CheckState.Unchecked, check_text=None):

        d = OptionalMessageDialog(parent, StyleMessage, title, msg, check_state=check_state, check_text=check_text)

        d.exec()
        return d.theCheckBox.isChecked()

    @staticmethod
    def question(parent, title, msg, check_state=QtCore.Qt.CheckState.Unchecked, check_text=None):

        d = OptionalMessageDialog(parent, StyleQuestion, title, msg, check_state=check_state, check_text=check_text)

        d.exec()

        return d.was_accepted, d.theCheckBox.isChecked()
