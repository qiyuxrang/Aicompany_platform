"""Untrusted document XML must not resolve local external entities."""
import io
from pathlib import Path
import tempfile
import unittest

from lxml import etree


class DocumentXMLSecurityTests(unittest.TestCase):
    def test_default_compat_and_streaming_parsers_cannot_read_external_file(self):
        marker = "SYNTHETIC_LOCAL_ENTITY_CONTENT"
        with tempfile.TemporaryDirectory(prefix="portal-xml-security-") as temporary:
            source = Path(temporary) / "synthetic.txt"
            source.write_text(marker, encoding="utf-8")
            xml = f'<!DOCTYPE root [<!ENTITY external SYSTEM "{source.as_uri()}">]><root>&external;</root>'.encode()
            for parser in ("compat", "iterparse"):
                with self.subTest(parser=parser):
                    try:
                        if parser == "compat":
                            root = etree.fromstring(xml, etree.ETCompatXMLParser())
                        else:
                            root = list(etree.iterparse(io.BytesIO(xml)))[-1][1]
                        rendered = etree.tostring(root, encoding="unicode")
                    except etree.XMLSyntaxError:
                        rendered = "rejected_external_entity"
                    self.assertNotIn(marker, rendered)

    def test_internal_entities_and_normal_document_xml_remain_supported(self):
        xml = b'<!DOCTYPE root [<!ENTITY internal "synthetic">]><root>&internal;</root>'
        self.assertEqual(etree.fromstring(xml, etree.ETCompatXMLParser()).text, "synthetic")


if __name__ == "__main__":
    unittest.main()
