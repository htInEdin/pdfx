from __future__ import absolute_import, division, print_function

import os
import pdfx
import pytest

curdir = os.path.dirname(os.path.realpath(__file__))


def test_all():
    with pytest.raises(pdfx.exceptions.FileNotFoundError):
        pdfx.PDFx("asd")

    with pytest.raises(pdfx.exceptions.DownloadError):
        pdfx.PDFx("http://invalid.com/404.pdf")

    with pytest.raises(pdfx.exceptions.PDFInvalidError):
        pdfx.PDFx(os.path.join(curdir, "pdfs/invalid.pdf"))

    pdf = pdfx.PDFx(os.path.join(curdir, "pdfs/valid.pdf"))
    urls = pdf.get_references_as_dict()
    assert len(urls['annot']) == 28
    assert len(urls['scrape']) == 31
    # pdf.download_pdfs("/tmp/")


def test_two_pdfs():
    # See https://github.com/metachris/pdfx/issues/14
    pdfx.PDFx(os.path.join(curdir, "pdfs/i14doc1.pdf"))
    pdf_2 = pdfx.PDFx(os.path.join(curdir, "pdfs/i14doc2.pdf"))
    assert len(pdf_2.get_references()) == 1 # Should be 2, will be on pdfminer
                                           # develop branch once
                       # https://github.com/pdfminer/pdfminer.six/issues/615 is fixed



