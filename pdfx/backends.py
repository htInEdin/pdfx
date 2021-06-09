# -*- coding: utf-8 -*-
"""
PDF Backend: pdfMiner
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sys
import logging
import warnings
from io import BytesIO, TextIOWrapper

# Character Detection Helper
import chardet

# Find URLs in text via regex
from . import extractor
from .libs.xmp import xmp_to_dict

# Setting `psparser.STRICT` is the first thing to do because it is
# referenced in the other pdfparser modules
from pdfminer import settings as pdfminer_settings

pdfminer_settings.STRICT = False
from pdfminer import psparser  # noqa: E402
from pdfminer.pdfdocument import PDFDocument  # noqa: E402
from pdfminer.pdfparser import PDFParser  # noqa: E402
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter  # noqa: E402
from pdfminer.pdfpage import PDFPage  # noqa: E402
from pdfminer.pdftypes import resolve1, PDFObjRef  # noqa: E402
from pdfminer.converter import TextConverter  # noqa: E402
from pdfminer.layout import LAParams  # noqa: E402

logger = logging.getLogger(__name__)
# logging.basicConfig(
#        level=logging.DEBUG,
#        format='%(levelname)s - %(module)s - %(message)s')

IS_PY2 = sys.version_info < (3, 0)
if not IS_PY2:
    # Python 3
    unicode = str


def make_compat_str(in_str):
    """
    Tries to guess encoding of [str/bytes] and
    return a standard unicode string
    """
    assert isinstance(in_str, (bytes, str, unicode))
    if not in_str:
        return unicode()

    # Chardet in Py2 works on str + bytes objects
    if IS_PY2 and isinstance(in_str, unicode):
        return in_str

    # Chardet in Py3 works on bytes objects
    if not IS_PY2 and not isinstance(in_str, bytes):
        return in_str

    # Detect the encoding now
    enc = chardet.detect(in_str)

    # Decode the object into a unicode object
    out_str = in_str.decode(enc["encoding"])

    # Cleanup
    if enc["encoding"] == "UTF-16BE":
        # Remove byte order marks (BOM)
        if out_str.startswith("\ufeff"):
            out_str = out_str[1:]
    return out_str


class Reference(object):
    """ Generic Reference """

    ref = ""
    page = 0
    # reftype is now deprecated.
    # It is always set to 'url' (for backwards compatability in case
    # anyone references it), but the old attempt to diagnose by filetype/regexp
    # was at best misleading and at worst just plain wrong
    _reftype = 'url'

    @property
    def reftype(self):
        warnings.warn("""Getting the reftype of a Reference is deprecated.
        All References are now created with a reftype of 'url'.""",
                      DeprecationWarning,
                      stacklevel=2)
        return self._reftype

    @reftype.setter
    def reftype(self, value):
        self._reftype = value
        warnings.warn("""Setting the reftype of a Reference is deprecated,
        no pdfx functionality pays attention to it any more.""",
                      DeprecationWarning,
                      stacklevel=2)

    def __init__(self, uri, page=0):
        self.ref = uri
        self.page = page

    def __hash__(self):
        return hash(self.ref)

    def __eq__(self, other):
        assert isinstance(other, Reference)
        return self.ref == other.ref

    def __lt__(self, other):
        assert isinstance(other, Reference)
        return self.ref < other.ref

    def __str__(self):
        return "<%s: %s>" % (self.reftype, self.ref)


def maybe_sort(r, s):
    # for use in get_references_as_dict
    return sorted(r) if s else r


class ReaderBackend(object):
    """
    Base class of all Readers (eg. for PDF files, text, etc.)

    The job of a Reader is to extract Text and Links.

    HST modified on 2019-02-18 to separate scraped from annotated refs
    """

    text = ""
    metadata = {}
    references = set()

    def __init__(self):
        self.text = ""
        self.metadata = {}
        self.references = set()
        self.scraped = set()
        self.refDicts = {True: None, False: None}
        self.refLists = {True: None, False: None}

    def get_metadata(self):
        return self.metadata

    def metadata_key_cleanup(self, d, k):
        """ Recursively clean metadata dictionaries """
        if isinstance(d[k], (str, unicode)):
            d[k] = d[k].strip()
            if not d[k]:
                del d[k]
        elif isinstance(d[k], (list, tuple)):
            new_list = []
            for item in d[k]:
                if isinstance(item, (str, unicode)):
                    if item.strip():
                        new_list.append(item.strip())
                elif item:
                    new_list.append(item)
            d[k] = new_list
            if len(d[k]) == 0:
                del d[k]
        elif isinstance(d[k], dict):
            for k2 in list(d[k].keys()):
                self.metadata_key_cleanup(d[k], k2)

    def metadata_cleanup(self):
        """ Clean metadata (delete all metadata fields without values) """
        for k in list(self.metadata.keys()):
            self.metadata_key_cleanup(self.metadata, k)

    def get_text(self):
        return self.text

    def get_references_as_dict(self, reftype=None, sort=False):
        if reftype is not None:
            warnings.warn("""The reftype argument is deprecated.
            All refs now have a reftype of url so it serves no purpose.""",
                          DeprecationWarning,
                          stacklevel=2)
        if self.refDicts[sort] is None:
            ret = {}
            if self.references:
                ret['annot'] = [r.ref for r in maybe_sort(self.references, sort)]
            if self.scraped:
                ret['scrape'] = [r.ref for r in maybe_sort(self.scraped, sort)]
            self.refDicts[sort] = ret
        return self.refDicts[sort]

    def get_references(self, reftype=None, sort=False):
        # Fake it as cli.py depends on this
        if reftype is not None:
            warnings.warn("""The reftype argument is deprecated.
            All refs now have a reftype of url so it serves no purpose.""",
                          DeprecationWarning,
                          stacklevel=2)
        if self.refLists[sort] is None:
            self.refLists[sort] = [r.ref for r in
                                   maybe_sort(self.references.union(self.scraped),
                                              sort)]
        return self.refLists[sort]


class PDFMinerBackend(ReaderBackend):
    """HST modified on 2019-02-18 to separate scraped from annotated refs and
    improve coverage"""
    def __init__(self, pdf_stream, password="", pagenos=[], maxpages=0):  # noqa: C901
        ReaderBackend.__init__(self)
        self.pdf_stream = pdf_stream

        # Extract Metadata
        parser = PDFParser(pdf_stream)
        self.doc = doc = PDFDocument(parser, password=password, caching=True)
        if doc.info:
            for k in doc.info[0]:
                v = doc.info[0][k]
                # print(repr(v), type(v))
                if isinstance(v, (bytes, str, unicode)):
                    self.metadata[k] = make_compat_str(v)
                elif isinstance(v, (psparser.PSLiteral, psparser.PSKeyword)):
                    self.metadata[k] = make_compat_str(v.name)

        # Secret Metadata
        if "Metadata" in doc.catalog:
            metadata = resolve1(doc.catalog["Metadata"]).get_data()
            # print(metadata)  # The raw XMP metadata
            # print(xmp_to_dict(metadata))
            self.metadata.update(xmp_to_dict(metadata))
            # print("---")

        # Extract Content
        text_io = TextIOWrapper(BytesIO())
        rsrcmgr = PDFResourceManager(caching=True)
        converter = TextConverter(rsrcmgr, text_io,
                                  laparams=LAParams(), imagewriter=None)
        interpreter = PDFPageInterpreter(rsrcmgr, converter)

        self.metadata["Pages"] = 0
        self.curpage = 0
        for page in PDFPage.get_pages(
            self.pdf_stream,
            pagenos=pagenos,
            maxpages=maxpages,
            password=password,
            caching=True,
            check_extractable=False,
        ):
            # Read page contents
            interpreter.process_page(page)
            self.metadata["Pages"] += 1
            self.curpage += 1

            # Collect URL annotations
            try:
                if page.annots:
                    refs = self.resolve_PDFObjRef(page.annots, False)
                    if refs:
                        if isinstance(refs, list):
                            for ref in refs:
                                if ref:
                                    self.references.add(ref)
                        elif isinstance(refs, Reference):
                            self.references.add(refs)
            except Exception as e:
                logger.warning(str(e))
                raise

        # Remove empty metadata entries
        self.metadata_cleanup()

        # Get text from stream
        text_io.seek(0)
        self.text = text_io.read()
        text_io.close()
        converter.close()
        # print(self.text)

        # Extract URL references from text
        for url in extractor.extract_urls(self.text):
            self.scraped.add(Reference(url, self.curpage))

        # for ref in extractor.extract_arxiv(self.text):
        #    self.references.add(Reference(ref, self.curpage))

        for ref in extractor.extract_doi(self.text):
            self.scraped.add(Reference('doi:'+ref, self.curpage))

    def resolve_PDFObjRef(self, obj_ref, internal):
        """
        Resolves PDFObjRef objects. Returns either None, a Reference object or
        a list of Reference objects.
        """
        if isinstance(obj_ref, list):
            return [self.resolve_PDFObjRef(item, True) for item in obj_ref]

        if isinstance(obj_ref, PDFObjRef):
            obj_resolved = obj_ref.resolve()
        elif internal:
            obj_resolved = obj_ref
        else:
            logger.warning("top-level type not of PDFObjRef: %s" % type(obj_ref))
            return None

        if isinstance(obj_resolved, bytes):
            if obj_resolved[-1] == 0:
                # This occurs once in 100 files or so...
                obj_resolved = obj_resolved.rstrip(b'\x00')
            try:
                obj_resolved = obj_resolved.decode("utf-8")
            except UnicodeDecodeError:
                obj_resolved = obj_resolved.decode("iso-8859-1")

        if isinstance(obj_resolved, (str, unicode)):
            return Reference(obj_resolved, self.curpage)

        if isinstance(obj_resolved, list):
            return [self.resolve_PDFObjRef(o, True) for o in obj_resolved]

        if "URI" in obj_resolved:
            return self.resolve_PDFObjRef(obj_resolved["URI"], True)

        if "A" in obj_resolved:
            return self.resolve_PDFObjRef(obj_resolved["A"], True)


class TextBackend(ReaderBackend):
    def __init__(self, stream):
        ReaderBackend.__init__(self)
        self.text = stream.read()

        # Extract URL references from text
        for url in extractor.extract_urls(self.text):
            self.references.add(Reference(url))

        for ref in extractor.extract_arxiv(self.text):
            self.references.add(Reference(ref))

        for ref in extractor.extract_doi(self.text):
            self.references.add(Reference(ref))
