"""A PyQt5 dialog to show ID log and progress"""

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

from PyQt5 import QtCore, QtWidgets, uic

from comictaggerlib.settings import ComicTaggerSettings
from comictaggerlib.ui.qtutils import reduce_widget_font_size

logger = logging.getLogger(__name__)


class IDProgressWindow(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)

        uic.loadUi(ComicTaggerSettings.get_ui_file("progresswindow.ui"), self)

        self.setWindowFlags(
            QtCore.Qt.WindowType(
                self.windowFlags()
                | QtCore.Qt.WindowType.WindowSystemMenuHint
                | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            )
        )

        reduce_widget_font_size(self.textEdit)
