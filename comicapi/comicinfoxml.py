"""A class to encapsulate ComicRack's ComicInfo.xml data"""

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
import xml.etree.ElementTree as ET

from comicapi import utils
from comicapi.genericmetadata import GenericMetadata
from comicapi.issuestring import IssueString

logger = logging.getLogger(__name__)


class ComicInfoXml:

    writer_synonyms = ["writer", "plotter", "scripter"]
    penciller_synonyms = ["artist", "penciller", "penciler", "breakdowns"]
    inker_synonyms = ["inker", "artist", "finishes"]
    colorist_synonyms = ["colorist", "colourist", "colorer", "colourer"]
    letterer_synonyms = ["letterer"]
    cover_synonyms = ["cover", "covers", "coverartist", "cover artist"]
    editor_synonyms = ["editor"]

    def get_parseable_credits(self):
        parsable_credits = []
        parsable_credits.extend(self.writer_synonyms)
        parsable_credits.extend(self.penciller_synonyms)
        parsable_credits.extend(self.inker_synonyms)
        parsable_credits.extend(self.colorist_synonyms)
        parsable_credits.extend(self.letterer_synonyms)
        parsable_credits.extend(self.cover_synonyms)
        parsable_credits.extend(self.editor_synonyms)
        return parsable_credits

    def metadata_from_string(self, string):

        tree = ET.ElementTree(ET.fromstring(string))
        return self.convert_xml_to_metadata(tree)

    def string_from_metadata(self, metadata, xml=None):
        tree = self.convert_metadata_to_xml(self, metadata, xml)
        tree_str = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True).decode()
        return tree_str

    def convert_metadata_to_xml(self, filename, metadata, xml=None):

        # shorthand for the metadata
        md = metadata

        if xml:
            root = ET.ElementTree(ET.fromstring(xml)).getroot()
        else:
            # build a tree structure
            root = ET.Element("ComicInfo")
            root.attrib["xmlns:xsi"] = "http://www.w3.org/2001/XMLSchema-instance"
            root.attrib["xmlns:xsd"] = "http://www.w3.org/2001/XMLSchema"
        # helper func

        def assign(cix_entry, md_entry):
            if md_entry is not None and md_entry:
                et_entry = root.find(cix_entry)
                if et_entry is not None:
                    et_entry.text = str(md_entry)
                else:
                    ET.SubElement(root, cix_entry).text = str(md_entry)
            else:
                et_entry = root.find(cix_entry)
                if et_entry is not None:
                    et_entry.clear()

        assign("Title", md.title)
        assign("Series", md.series)
        assign("Number", md.issue)
        assign("Count", md.issue_count)
        assign("Volume", md.volume)
        assign("AlternateSeries", md.alternate_series)
        assign("AlternateNumber", md.alternate_number)
        assign("StoryArc", md.story_arc)
        assign("SeriesGroup", md.series_group)
        assign("AlternateCount", md.alternate_count)
        assign("Summary", md.comments)
        assign("Notes", md.notes)
        assign("Year", md.year)
        assign("Month", md.month)
        assign("Day", md.day)

        # need to specially process the credits, since they are structured
        # differently than CIX
        credit_writer_list = []
        credit_penciller_list = []
        credit_inker_list = []
        credit_colorist_list = []
        credit_letterer_list = []
        credit_cover_list = []
        credit_editor_list = []

        # first, loop thru credits, and build a list for each role that CIX
        # supports
        for credit in metadata.credits:

            if credit["role"].lower() in set(self.writer_synonyms):
                credit_writer_list.append(credit["person"].replace(",", ""))

            if credit["role"].lower() in set(self.penciller_synonyms):
                credit_penciller_list.append(credit["person"].replace(",", ""))

            if credit["role"].lower() in set(self.inker_synonyms):
                credit_inker_list.append(credit["person"].replace(",", ""))

            if credit["role"].lower() in set(self.colorist_synonyms):
                credit_colorist_list.append(credit["person"].replace(",", ""))

            if credit["role"].lower() in set(self.letterer_synonyms):
                credit_letterer_list.append(credit["person"].replace(",", ""))

            if credit["role"].lower() in set(self.cover_synonyms):
                credit_cover_list.append(credit["person"].replace(",", ""))

            if credit["role"].lower() in set(self.editor_synonyms):
                credit_editor_list.append(credit["person"].replace(",", ""))

        # second, convert each list to string, and add to XML struct
        assign("Writer", utils.list_to_string(credit_writer_list))

        assign("Penciller", utils.list_to_string(credit_penciller_list))

        assign("Inker", utils.list_to_string(credit_inker_list))

        assign("Colorist", utils.list_to_string(credit_colorist_list))

        assign("Letterer", utils.list_to_string(credit_letterer_list))

        assign("CoverArtist", utils.list_to_string(credit_cover_list))

        assign("Editor", utils.list_to_string(credit_editor_list))

        assign("Publisher", md.publisher)
        assign("Imprint", md.imprint)
        assign("Genre", md.genre)
        assign("Web", md.web_link)
        assign("PageCount", md.page_count)
        assign("LanguageISO", md.language)
        assign("Format", md.format)
        assign("AgeRating", md.maturity_rating)
        assign("BlackAndWhite", "Yes" if md.black_and_white else None)
        assign("Manga", md.manga)
        assign("Characters", md.characters)
        assign("Teams", md.teams)
        assign("Locations", md.locations)
        assign("ScanInformation", md.scan_info)

        #  loop and add the page entries under pages node
        pages_node = root.find("Pages")
        if pages_node is not None:
            pages_node.clear()
        else:
            pages_node = ET.SubElement(root, "Pages")

        for page_dict in md.pages:
            page_node = ET.SubElement(pages_node, "Page")
            page_node.attrib = dict(sorted(page_dict.items()))

        utils.indent(root)

        # wrap it in an ElementTree instance, and save as XML
        tree = ET.ElementTree(root)
        return tree

    def convert_xml_to_metadata(self, tree):

        root = tree.getroot()

        if root.tag != "ComicInfo":
            raise "1"

        def get(name):
            tag = root.find(name)
            if tag is None:
                return None
            return tag.text

        md = GenericMetadata()

        md.series = utils.xlate(get("Series"))
        md.title = utils.xlate(get("Title"))
        md.issue = IssueString(utils.xlate(get("Number"))).as_string()
        md.issue_count = utils.xlate(get("Count"), True)
        md.volume = utils.xlate(get("Volume"), True)
        md.alternate_series = utils.xlate(get("AlternateSeries"))
        md.alternate_number = IssueString(utils.xlate(get("AlternateNumber"))).as_string()
        md.alternate_count = utils.xlate(get("AlternateCount"), True)
        md.comments = utils.xlate(get("Summary"))
        md.notes = utils.xlate(get("Notes"))
        md.year = utils.xlate(get("Year"), True)
        md.month = utils.xlate(get("Month"), True)
        md.day = utils.xlate(get("Day"), True)
        md.publisher = utils.xlate(get("Publisher"))
        md.imprint = utils.xlate(get("Imprint"))
        md.genre = utils.xlate(get("Genre"))
        md.web_link = utils.xlate(get("Web"))
        md.language = utils.xlate(get("LanguageISO"))
        md.format = utils.xlate(get("Format"))
        md.manga = utils.xlate(get("Manga"))
        md.characters = utils.xlate(get("Characters"))
        md.teams = utils.xlate(get("Teams"))
        md.locations = utils.xlate(get("Locations"))
        md.page_count = utils.xlate(get("PageCount"), True)
        md.scan_info = utils.xlate(get("ScanInformation"))
        md.story_arc = utils.xlate(get("StoryArc"))
        md.series_group = utils.xlate(get("SeriesGroup"))
        md.maturity_rating = utils.xlate(get("AgeRating"))

        tmp = utils.xlate(get("BlackAndWhite"))
        if tmp is not None and tmp.lower() in ["yes", "true", "1"]:
            md.black_and_white = True
        # Now extract the credit info
        for n in root:
            if any(
                [
                    n.tag == "Writer",
                    n.tag == "Penciller",
                    n.tag == "Inker",
                    n.tag == "Colorist",
                    n.tag == "Letterer",
                    n.tag == "Editor",
                ]
            ):
                if n.text is not None:
                    for name in n.text.split(","):
                        md.add_credit(name.strip(), n.tag)

            if n.tag == "CoverArtist":
                if n.text is not None:
                    for name in n.text.split(","):
                        md.add_credit(name.strip(), "Cover")

        # parse page data now
        pages_node = root.find("Pages")
        if pages_node is not None:
            for page in pages_node:
                md.pages.append(page.attrib)

        md.is_empty = False

        return md

    def write_to_external_file(self, filename, metadata, xml=None):

        tree = self.convert_metadata_to_xml(self, metadata, xml)
        tree.write(filename, encoding="utf-8", xml_declaration=True)

    def read_from_external_file(self, filename):

        tree = ET.parse(filename)
        return self.convert_xml_to_metadata(tree)
