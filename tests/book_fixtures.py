"""Trimmed Open Library and Library of Congress records for the book tests.

Shaped after live lookups of ISBN 9780374619329 (the plan's worked
example): the Open Library edition is retailer-fed and carries the LCCN,
and the MARCXML is the Cataloging in Publication record that LCCN leads to.
"""

from typing import Any

ISBN = "9780374619329"
LCCN = "2025007165"

EDITION: dict[str, Any] = {
    "key": "/books/OL58000000M",
    "title": "Enshittification",
    "subtitle": "Why Everything Suddenly Got Worse and What to Do About It",
    "authors": [{"key": "/authors/OL1394244A"}],
    "publishers": ["MCD / Farrar, Straus and Giroux"],
    "publish_places": ["New York, USA"],
    "publish_date": "Oct 21, 2025",
    "isbn_10": ["0374619328"],
    "isbn_13": [ISBN],
    "lccn": [LCCN],
    "source_records": ["amazon:0374619328"],
    "works": [{"key": "/works/OL40000000W"}],
    "number_of_pages": 352,
    "physical_format": "hardcover",
}

AUTHOR: dict[str, Any] = {"key": "/authors/OL1394244A", "name": "Cory Doctorow"}

# An edition a library catalog fed, with no LCCN and no subtitle.
CATALOG_EDITION: dict[str, Any] = {
    "key": "/books/OL1M",
    "title": "Weapons of Math Destruction",
    "by_statement": "Cathy O'Neil.",
    "publishers": ["Crown"],
    "publish_places": ["New York"],
    "publish_date": "2016",
    "isbn_13": ["9780553418811"],
    "source_records": ["marc:marc_loc_2016/BooksAll.2016.part41.utf8:1"],
}

SEARCH_PAGE: dict[str, Any] = {
    "numFound": 1,
    "start": 0,
    "docs": [
        {
            "key": "/works/OL40000000W",
            "title": "Enshittification",
            "subtitle": "Why Everything Suddenly Got Worse and What to Do About It",
            "author_name": ["Cory Doctorow"],
            "first_publish_year": 2025,
            "isbn": [ISBN, "0374619328"],
            "publisher": ["MCD"],
        }
    ],
}

MARCXML = f"""<?xml version="1.0" encoding="UTF-8"?>
<record xmlns="http://www.loc.gov/MARC21/slim">
  <leader>01234cam a2200301 i 4500</leader>
  <controlfield tag="001">23456789</controlfield>
  <controlfield tag="008">250312s2025    nyu      b    001 0 eng  </controlfield>
  <datafield tag="010" ind1=" " ind2=" ">
    <subfield code="a">  {LCCN}</subfield>
  </datafield>
  <datafield tag="020" ind1=" " ind2=" ">
    <subfield code="a">9780374619329</subfield>
    <subfield code="q">(hardcover)</subfield>
  </datafield>
  <datafield tag="020" ind1=" " ind2=" ">
    <subfield code="a">0374619328 (hardcover)</subfield>
  </datafield>
  <datafield tag="040" ind1=" " ind2=" ">
    <subfield code="a">DLC</subfield>
    <subfield code="b">eng</subfield>
  </datafield>
  <datafield tag="100" ind1="1" ind2=" ">
    <subfield code="a">Doctorow, Cory,</subfield>
    <subfield code="e">author.</subfield>
  </datafield>
  <datafield tag="245" ind1="1" ind2="0">
    <subfield code="a">Enshittification :</subfield>
    <subfield code="b">why everything suddenly got worse and what to do about it \
/</subfield>
    <subfield code="c">Cory Doctorow.</subfield>
  </datafield>
  <datafield tag="250" ind1=" " ind2=" ">
    <subfield code="a">First edition.</subfield>
  </datafield>
  <datafield tag="264" ind1=" " ind2="1">
    <subfield code="a">New York :</subfield>
    <subfield code="b">MCD, Farrar, Straus and Giroux,</subfield>
    <subfield code="c">2025.</subfield>
  </datafield>
  <datafield tag="264" ind1=" " ind2="4">
    <subfield code="c">©2025</subfield>
  </datafield>
</record>
"""

# The same record as the SRU gateway wraps it: `<zs:record>` envelopes whose
# local name is also `record`, around the MARC record.
SRU_RESPONSE = f"""<?xml version="1.0"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
<zs:version>1.1</zs:version><zs:numberOfRecords>1</zs:numberOfRecords>
<zs:records><zs:record><zs:recordSchema>marcxml</zs:recordSchema>
<zs:recordPacking>xml</zs:recordPacking><zs:recordData>
{MARCXML.split("?>", 1)[1]}
</zs:recordData><zs:recordPosition>1</zs:recordPosition></zs:record></zs:records>
</zs:searchRetrieveResponse>
"""

SRU_EMPTY = """<?xml version="1.0"?>
<zs:searchRetrieveResponse xmlns:zs="http://www.loc.gov/zing/srw/">
<zs:version>1.1</zs:version><zs:numberOfRecords>0</zs:numberOfRecords>
</zs:searchRetrieveResponse>
"""

# An older record: `260` instead of `264`, a corporate added entry, and a
# bracketed (cataloger-supplied) place and date.
MARCXML_260 = """<collection xmlns="http://www.loc.gov/MARC21/slim">
<record>
  <leader>00000cam a2200000 a 4500</leader>
  <datafield tag="245" ind1="1" ind2="0">
    <subfield code="a">A report on things.</subfield>
  </datafield>
  <datafield tag="260" ind1=" " ind2=" ">
    <subfield code="a">[Washington, D.C.] :</subfield>
    <subfield code="b">The Office ;</subfield>
    <subfield code="c">[1999]</subfield>
  </datafield>
  <datafield tag="710" ind1="2" ind2=" ">
    <subfield code="a">Some Agency.</subfield>
  </datafield>
</record>
</collection>
"""

BOOK_BIB = f"""@book{{doctorow2025enshittification,
  author = {{Doctorow, Cory}},
  year = {{2025}},
  title = {{Enshittification}},
  publisher = {{MCD}},
  isbn = {{{ISBN}}},
}}
"""
