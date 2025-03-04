"""A PyQt5 widget to display cover images

Display cover images from either a local archive, or from Comic Vine.
TODO: This should be re-factored using subclasses!
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

from PyQt5 import QtCore, QtGui, QtWidgets, uic

from comicapi.comicarchive import ComicArchive
from comictaggerlib.comicvinetalker import ComicVineTalker
from comictaggerlib.imagefetcher import ImageFetcher
from comictaggerlib.imagepopup import ImagePopup
from comictaggerlib.pageloader import PageLoader
from comictaggerlib.settings import ComicTaggerSettings
from comictaggerlib.ui.qtutils import get_qimage_from_data, reduce_widget_font_size

logger = logging.getLogger(__name__)


def clickable(widget):
    """Allow a label to be clickable"""

    class Filter(QtCore.QObject):

        dblclicked = QtCore.pyqtSignal()

        def eventFilter(self, obj, event):

            if obj == widget:
                if event.type() == QtCore.QEvent.Type.MouseButtonDblClick:
                    self.dblclicked.emit()
                    return True

            return False

    flt = Filter(widget)
    widget.installEventFilter(flt)
    return flt.dblclicked


class Signal(QtCore.QObject):
    alt_url_list_fetch_complete = QtCore.pyqtSignal(list)
    url_fetch_complete = QtCore.pyqtSignal(str, str)
    image_fetch_complete = QtCore.pyqtSignal(QtCore.QByteArray)

    def __init__(self, list_fetch, url_fetch, image_fetch):
        super().__init__()
        self.alt_url_list_fetch_complete.connect(list_fetch)
        self.url_fetch_complete.connect(url_fetch)
        self.image_fetch_complete.connect(image_fetch)

    def emit_list(self, url_list: list):
        self.alt_url_list_fetch_complete.emit(url_list)

    def emit_url(self, image_url: str, thumb_url: str):
        self.url_fetch_complete.emit(image_url, thumb_url)

    def emit_image(self, image_data: QtCore.QByteArray):
        self.image_fetch_complete.emit(image_data)


class CoverImageWidget(QtWidgets.QWidget):
    ArchiveMode = 0
    AltCoverMode = 1
    URLMode = 1
    DataMode = 3

    def __init__(self, parent, mode, expand_on_click=True):
        super().__init__(parent)

        uic.loadUi(ComicTaggerSettings.get_ui_file("coverimagewidget.ui"), self)

        reduce_widget_font_size(self.label)

        self.sig = Signal(
            self.alt_cover_url_list_fetch_complete, self.primary_url_fetch_complete, self.cover_remote_fetch_complete
        )

        self.mode = mode
        self.page_loader = None
        self.showControls = True

        self.current_pixmap = QtGui.QPixmap()

        self.comic_archive = None
        self.issue_id = None
        self.cover_fetcher = None
        self.url_list = []
        if self.page_loader is not None:
            self.page_loader.abandoned = True
        self.page_loader = None
        self.imageIndex = -1
        self.imageCount = 1
        self.imageData = None

        self.btnLeft.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("left.png")))
        self.btnRight.setIcon(QtGui.QIcon(ComicTaggerSettings.get_graphic("right.png")))

        self.btnLeft.clicked.connect(self.decrement_image)
        self.btnRight.clicked.connect(self.increment_image)
        if expand_on_click:
            clickable(self.lblImage).connect(self.show_popup)
        else:
            self.lblImage.setToolTip("")

        self.update_content()

    def reset_widget(self):
        self.comic_archive = None
        self.issue_id = None
        self.cover_fetcher = None
        self.url_list = []
        if self.page_loader is not None:
            self.page_loader.abandoned = True
        self.page_loader = None
        self.imageIndex = -1
        self.imageCount = 1
        self.imageData = None

    def clear(self):
        self.reset_widget()
        self.update_content()

    def increment_image(self):
        self.imageIndex += 1
        if self.imageIndex == self.imageCount:
            self.imageIndex = 0
        self.update_content()

    def decrement_image(self):
        self.imageIndex -= 1
        if self.imageIndex == -1:
            self.imageIndex = self.imageCount - 1
        self.update_content()

    def set_archive(self, ca: ComicArchive, page=0):
        if self.mode == CoverImageWidget.ArchiveMode:
            self.reset_widget()
            self.comic_archive = ca
            self.imageIndex = page
            self.imageCount = ca.get_number_of_pages()
            self.update_content()

    def set_url(self, url):
        if self.mode == CoverImageWidget.URLMode:
            self.reset_widget()
            self.update_content()

            self.url_list = [url]
            self.imageIndex = 0
            self.imageCount = 1
            self.update_content()

    def set_issue_id(self, issue_id):
        if self.mode == CoverImageWidget.AltCoverMode:
            self.reset_widget()
            self.update_content()
            self.issue_id = issue_id

            comic_vine = ComicVineTalker()
            comic_vine.url_fetch_complete = self.sig.emit_url
            comic_vine.async_fetch_issue_cover_urls(int(self.issue_id))

    def set_image_data(self, image_data):
        if self.mode == CoverImageWidget.DataMode:
            self.reset_widget()

            if image_data is None:
                self.imageIndex = -1
            else:
                self.imageIndex = 0
                self.imageData = image_data

            self.update_content()

    def primary_url_fetch_complete(self, primary_url, thumb_url):
        self.url_list.append(str(primary_url))
        self.imageIndex = 0
        self.imageCount = len(self.url_list)
        self.update_content()

        # defer the alt cover search
        QtCore.QTimer.singleShot(1, self.start_alt_cover_search)

    def start_alt_cover_search(self):

        # now we need to get the list of alt cover URLs
        self.label.setText("Searching for alt. covers...")

        # page URL should already be cached, so no need to defer
        comic_vine = ComicVineTalker()
        issue_page_url = comic_vine.fetch_issue_page_url(self.issue_id)
        comic_vine.alt_url_list_fetch_complete = self.sig.emit_list
        comic_vine.async_fetch_alternate_cover_urls(int(self.issue_id), issue_page_url)

    def alt_cover_url_list_fetch_complete(self, url_list):
        if len(url_list) > 0:
            self.url_list.extend(url_list)
            self.imageCount = len(self.url_list)
        self.update_controls()

    def set_page(self, pagenum):
        if self.mode == CoverImageWidget.ArchiveMode:
            self.imageIndex = pagenum
            self.update_content()

    def update_content(self):
        self.update_image()
        self.update_controls()

    def update_image(self):
        if self.imageIndex == -1:
            self.load_default()
        elif self.mode in [CoverImageWidget.AltCoverMode, CoverImageWidget.URLMode]:
            self.load_url()
        elif self.mode == CoverImageWidget.DataMode:
            self.cover_remote_fetch_complete(self.imageData)
        else:
            self.load_page()

    def update_controls(self):
        if not self.showControls or self.mode == CoverImageWidget.DataMode:
            self.btnLeft.hide()
            self.btnRight.hide()
            self.label.hide()
            return

        if self.imageIndex == -1 or self.imageCount == 1:
            self.btnLeft.setEnabled(False)
            self.btnRight.setEnabled(False)
            self.btnLeft.hide()
            self.btnRight.hide()
        else:
            self.btnLeft.setEnabled(True)
            self.btnRight.setEnabled(True)
            self.btnLeft.show()
            self.btnRight.show()

        if self.imageIndex == -1 or self.imageCount == 1:
            self.label.setText("")
        elif self.mode == CoverImageWidget.AltCoverMode:
            self.label.setText(f"Cover {self.imageIndex + 1} (of {self.imageCount})")
        else:
            self.label.setText(f"Page {self.imageIndex + 1} (of {self.imageCount})")

    def load_url(self):
        self.load_default()
        self.cover_fetcher = ImageFetcher()
        self.cover_fetcher.image_fetch_complete = self.sig.emit_image
        self.cover_fetcher.fetch(self.url_list[self.imageIndex])

    # called when the image is done loading from internet
    def cover_remote_fetch_complete(self, image_data):
        img = get_qimage_from_data(image_data)
        self.current_pixmap = QtGui.QPixmap.fromImage(img)
        self.set_display_pixmap()

    def load_page(self):
        if self.comic_archive is not None:
            if self.page_loader is not None:
                self.page_loader.abandoned = True
            self.page_loader = PageLoader(self.comic_archive, self.imageIndex)
            self.page_loader.loadComplete.connect(self.page_load_complete)
            self.page_loader.start()

    def page_load_complete(self, image_data):
        img = get_qimage_from_data(image_data)
        self.current_pixmap = QtGui.QPixmap.fromImage(img)
        self.set_display_pixmap()
        self.page_loader = None

    def load_default(self):
        self.current_pixmap = QtGui.QPixmap(ComicTaggerSettings.get_graphic("nocover.png"))
        self.set_display_pixmap()

    def resizeEvent(self, resize_event):
        if self.current_pixmap is not None:
            self.set_display_pixmap()

    def set_display_pixmap(self):
        """The deltas let us know what the new width and height of the label will be"""

        new_h = self.frame.height()
        new_w = self.frame.width()
        frame_w = self.frame.width()
        frame_h = self.frame.height()

        new_h -= 4
        new_w -= 4

        new_h = max(new_h, 0)
        new_w = max(new_w, 0)

        # scale the pixmap to fit in the frame
        scaled_pixmap = self.current_pixmap.scaled(new_w, new_h, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self.lblImage.setPixmap(scaled_pixmap)

        # move and resize the label to be centered in the fame
        img_w = scaled_pixmap.width()
        img_h = scaled_pixmap.height()
        self.lblImage.resize(img_w, img_h)
        self.lblImage.move(int((frame_w - img_w) / 2), int((frame_h - img_h) / 2))

    def show_popup(self):
        ImagePopup(self, self.current_pixmap)
