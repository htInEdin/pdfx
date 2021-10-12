# -*- coding: utf-8 -*-
"""
Extract metadata and links from a local or remote PDF, and
optionally download all referenced PDFs.

Features

* Extract metadata and PDF URLs from a given PDF
* Download all PDFs referenced in the original PDF
* Works with local and online pdfs
* Use as command-line tool or Python package
* Compatible with Python 2 and 3

Usage

PDFx can be used to extract infos from PDF in two ways:

* Command line tool `pdfx`
* Python library `import pdfx`

>>> import pdfx
>>> pdf = pdfx.PDFx("filename-or-url.pdf")
>>> metadata = pdf.get_metadata()
>>> references_list = pdf.get_references()
>>> references_dict = pdf.get_references_as_dict()
>>> pdf.download_pdfs("target-directory")

https://www.metachris.com/pdfx

Copyright (c) 2015, Chris Hager <chris@linuxuser.at>
License: GPLv3
"""
from __future__ import absolute_import, division, print_function, unicode_literals

__title__ = "pdfx"
__version__ = "1.4.1"
__author__ = "Chris Hager"
__license__ = "Apache 2.0"
__copyright__ = "Copyright 2015 Chris Hager"

import os
import sys
import json
import shutil
import logging


from .extractor import extract_urls
from .backends import PDFMinerBackend, TextBackend
from .downloader import download_urls
from .exceptions import FileNotFoundError, DownloadError, PDFInvalidError
from pdfminer.pdfparser import PDFSyntaxError

import stopit
logging.getLogger('stopit').setLevel(logging.ERROR)

IS_PY2 = sys.version_info < (3, 0)

if IS_PY2:
    # Python 2
    from cStringIO import StringIO as BytesIO
    from urllib2 import Request, urlopen
else:
    # Python 3
    from io import BytesIO
    from urllib.request import Request, urlopen

    unicode = str

logger = logging.getLogger(__name__)

class PDFTimeout(Exception):
    """Raised for any timeout while attempting to build a PDFx instance"""
    pass

class PDFReadTimeout(PDFTimeout):
    """Raised if reading the complete input for PDFx construction times out"""
    pass

class PDFTextTimeout(PDFTimeout):
    """Raised if extracting the text content for PDFx construction times out.
    Annotation data may have been constructed."""
    pass


class PDFx(object):
    """
    Main class which extracts infos from PDF

    General flow:
    * init -> get_metadata()

    In detail:
    >>> import pdfx
    >>> pdf = pdfx.PDFx("filename-or-url.pdf")
    >>> print(pdf.get_metadata())
    >>> print(pdf.get_tet())
    >>> print(pdf.get_references())
    >>> pdf.download_pdfs("target-directory")
    """

    # Available after init
    uri = None  # Original URI
    fn = None  # Filename part of URI
    is_url = False  # False if file
    is_pdf = True

    stream = None  # ByteIO Stream
    reader = None  # ReaderBackend
    summary = {}

    def buildStream(self):
        logger.debug("<buildStream")

        # Grab content of reference
        uri = self.uri
        if self.is_url:
            logger.debug("Reading url '%s'..." % uri)
            self.fn = uri.split("/")[-1]
            try:
                content = urlopen(Request(uri)).read()
                self.stream = BytesIO(content)
            except stopit.TimeoutException:
                logger.debug("buildStream timeout")
                raise
            except Exception as e:
                logger.debug("buildStream download error")
                raise DownloadError("Error downloading '%s' (%s)" % (uri, unicode(e)))

        else:
            if not os.path.isfile(uri):
                logger.debug("buildStream bad filename error")
                raise FileNotFoundError("Invalid filename and not an url: '%s'" % uri)
            self.fn = os.path.basename(uri)
            self.stream = open(uri, "rb")
        logger.debug("buildStream/>")


    def __init__(self, uri, readTimeout=None, textTimeout=None, limit=True):
        """
        Open PDF handle and parse PDF metadata
        - `uri` can be either a filename or an url
        readTimeout if present is a timeout after which we give
         up trying to read and raise PDFReadTimeout
        textTimeout if present is a timeout post-reading after which
          if limit we try again w/o LAParams (i.e we don't process and
                                              layout the text),
          otherwise we raise PDFTextTimeout
        textTimeout with limit makes sense if you only care about annotations,
          e.g. if you're looking for URI references
        """
        logger.debug("Init with uri: %s" % uri)

        self.uri = uri
        self.limited=False

        # Find out whether pdf is an URL or local file
        url = extract_urls(uri)
        self.is_url = len(url)

        if readTimeout is None:
            self.buildStream()
        else:
            if readTimeout <= 0:
                # Only makes sense for testing, but triggers bug in stopit
                # (see https://github.com/glenfant/stopit/issues/26)
                # so we fake it here
                cc = False
            else:
                with stopit.ThreadingTimeout(readTimeout) as cc:
                    self.buildStream()
            if not(cc):
                raise PDFReadTimeout

        # Create ReaderBackend instance
        try:
            if textTimeout is None:
                self.reader = PDFMinerBackend(self.stream)
            else:
                if textTimeout <= 0:
                    # see comment above
                    cc = False
                else:
                    with stopit.ThreadingTimeout(textTimeout) as cc:
                        self.reader = PDFMinerBackend(self.stream)
                if not(cc):
                    if limit is None:
                        raise PDFTextTimeout
                    else:
                        self.limited=True
                        self.reader=PDFMinerBackend(self.stream,
                                                    lap=None,annot_only=True)
        except PDFSyntaxError as e:
            raise PDFInvalidError("Invalid PDF (%s)" % unicode(e))

            # Could try to create a TextReader
            logger.info(unicode(e))
            logger.info("Trying to create a TextReader backend...")
            self.stream.seek(0)
            self.reader = TextBackend(self.stream)
            self.is_pdf = False
        except Exception as e:
            raise
            raise PDFInvalidError("Invalid PDF (%s)" % unicode(e))

        # Save metadata to user-supplied directory
        self.summary = {
            "source": {
                "type": "url" if self.is_url else "file",
                "location": self.uri,
                "filename": self.fn
            },
            "metadata": self.reader.get_metadata(),
        }

        # Search for URLs
        self.summary["references"] = self.reader.get_references_as_dict()
        # print(self.summary)

    def get_text(self):
        return self.reader.get_text()

    def get_metadata(self):
        return self.reader.get_metadata()

    def get_references(self, reftype=None, sort=False):
        """ reftype can be `None` for all, `pdf`, etc. """
        return self.reader.get_references(reftype=reftype, sort=sort)

    def get_references_as_dict(self, reftype=None, sort=False):
        """ reftype can be `None` for all, `pdf`, etc. """
        return self.reader.get_references_as_dict(reftype=reftype, sort=sort)

    def get_references_count(self, reftype=None):
        """ reftype can be `None` for all, `pdf`, etc. """
        r = self.reader.get_references(reftype=reftype)
        return len(r)

    def download_pdfs(self, target_dir):
        logger.debug("Download pdfs to %s" % target_dir)
        assert target_dir, "Need a download directory"
        assert not os.path.isfile(target_dir), "Download directory is a file"

        # Create output directory
        if target_dir and not os.path.exists(target_dir):
            os.makedirs(target_dir)
            logger.debug("Created output directory '%s'" % target_dir)

        # Save original PDF to user-supplied directory
        fn = os.path.join(target_dir, self.fn)
        with open(fn, "wb") as f:
            self.stream.seek(0)
            shutil.copyfileobj(self.stream, f)
        logger.debug("- Saved original pdf as '%s'" % fn)

        fn_json = "%s.infos.json" % fn
        with open(fn_json, "w") as f:
            f.write(json.dumps(self.summary, indent=2))
        logger.debug("- Saved metadata to '%s'" % fn_json)

        # Download references
        urls = [ref.ref for ref in self.get_references("pdf")]
        if not urls:
            return

        dir_referenced_pdfs = os.path.join(target_dir, "%s-referenced-pdfs" % self.fn)
        logger.debug("Downloading %s referenced pdfs..." % len(urls))

        # Download urls as a set to avoid duplicates
        download_urls(urls, dir_referenced_pdfs)
