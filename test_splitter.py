import unittest
from splitter import ParagraphSplitter


def run(chunks):
    splitter = ParagraphSplitter()
    result = [""]
    for chunk in chunks:
        for item in splitter.feed(chunk):
            if item is None:
                result.append("")
            else:
                result[-1] += item
    result[-1] += "".join(splitter.flush())
    return result


class SplitterTests(unittest.TestCase):
    def test_all_chunk_boundaries(self):
        text = "First\r\n \t\r\nSecond\nline\n\nThird"
        expected = ["First", "Second\nline", "Third"]
        for i in range(len(text) + 1):
            self.assertEqual(run([text[:i], text[i:]]), expected)
        self.assertEqual(run(list(text)), expected)

    def test_streams_without_waiting_for_whole_paragraph(self):
        s = ParagraphSplitter()
        self.assertEqual(s.feed("Hello"), ["Hello"])
        self.assertEqual(s.feed("\n"), [])
        self.assertEqual(s.feed("\n"), [])
        self.assertEqual(s.feed("world"), [None, "world"])

    def test_empty_tail_and_leading_blanks(self):
        self.assertEqual(run(list("\n\nFirst\n\n\nSecond\n\n")), ["First", "Second"])
        self.assertEqual(run(["\n\n"]), [""])

    def test_fenced_code(self):
        for marker in ["```", "~~~~"]:
            code = f"{marker}python\nx = 1\n\ny = 2\n{marker}"
            self.assertEqual(run(list("Before\n\n" + code + "\n\nAfter")), ["Before", code, "After"])

    def test_incomplete_fence(self):
        text = "```\nx\n\ny"
        self.assertEqual(run(list(text)), [text])

    def test_short_marker_does_not_close_fence(self):
        text = "````\nx\n```\n\ny\n````"
        self.assertEqual(run(list(text)), [text])

    def test_single_line_and_inline_code(self):
        self.assertEqual(run(list("A `x`\nB\n\nC")), ["A `x`\nB", "C"])


if __name__ == "__main__":
    unittest.main()
