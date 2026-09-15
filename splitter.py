"""Incremental blank-line boundaries. No AstrBot imports or extra dependencies."""
import re

BREAK = None


class ParagraphSplitter:
    def __init__(self):
        self.pending = ""
        self.line = ""
        self.fence = None
        self.has_text = False

    def feed(self, text):
        output = []
        chunk = ""
        for char in text:
            if char == "\n":
                match = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", self.line.rstrip("\r"))
                if match:
                    marker, suffix = match.groups()
                    if self.fence is None and not (marker[0] == "`" and "`" in suffix):
                        self.fence = (marker[0], len(marker))
                    elif self.fence and marker[0] == self.fence[0] and len(marker) >= self.fence[1] and not suffix.strip():
                        self.fence = None
                self.line = ""
            else:
                self.line += char

            if self.fence:
                chunk += self.pending + char
                self.pending = ""
                self.has_text = True
            elif char in "\r\n \t":
                self.pending += char
            else:
                if self.pending.count("\n") >= 2 and self.has_text:
                    if chunk:
                        output.append(chunk)
                        chunk = ""
                    output.append(BREAK)
                    self.pending = ""
                    self.has_text = False
                if self.has_text or self.pending.count("\n") < 2:
                    chunk += self.pending
                self.pending = ""
                chunk += char
                self.has_text = True
        if chunk:
            output.append(chunk)
        return output

    def flush(self):
        """Finish at EOF/control boundary; discard empty trailing paragraphs."""
        text = self.pending if self.has_text and self.pending.count("\n") < 2 else ""
        self.pending = ""
        return [text] if text else []
