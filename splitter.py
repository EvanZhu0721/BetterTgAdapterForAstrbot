"""Incremental separators with bounded regex lookahead and fenced-code protection."""
import re

try:
    from .separators import separator_rules
except ImportError:
    from separators import separator_rules

BREAK = None
_BLANK = re.compile(r'\r?\n[ \t]*\r?\n')
_TAIL_SPACE = re.compile(r'[ \t\r\n]*$')
_FENCE = re.compile(r' {0,3}(`{3,}|~{3,})(.*)')


class ParagraphSplitter:
    def __init__(self, rules=None):
        self.rules = rules or separator_rules()
        self.buffer = ''
        self.candidate = ''
        self.normal_line = False
        self.code_line = ''
        self.fence = None
        self.has_text = False
        self.boundary = False
        self.output = []
        self.leading_delimiters = ''

    def _emit(self, text, delimiter=False):
        if not text:
            return
        if delimiter:
            if not self.has_text:
                self.leading_delimiters += text
            elif self.output and self.output[-1] is not None:
                self.output[-1] += text
            else:
                self.output.append(text)
            return
        if not text.strip():
            if self.has_text and not self.boundary:
                self.output.append(text)
            return
        if self.boundary or not self.has_text:
            text = text.lstrip('\r\n')
        text = self.leading_delimiters + text
        self.leading_delimiters = ''
        if self.boundary and self.has_text:
            self.output.append(BREAK)
        self.boundary = False
        self.has_text = True
        if self.output and self.output[-1] is not None:
            self.output[-1] += text
        else:
            self.output.append(text)

    def _drain(self, final=False):
        while self.buffer:
            safe = len(self.buffer) if final else max(0, len(self.buffer) - self.rules.width + 1)
            if not final:
                safe = min(safe, _TAIL_SPACE.search(self.buffer).start())
            candidates = []
            for pattern, custom in [(_BLANK if self.rules.blank_lines else None, False), (self.rules.literal_pattern, True), (self.rules.advanced_pattern, True)]:
                if pattern is not None and (match := pattern.search(self.buffer)):
                    candidates.append((match.start(), -match.end(), custom, match))
            chosen = min(candidates, key=lambda item: item[:3]) if candidates else None
            if chosen is not None and (final or chosen[0] < safe):
                _, _, custom, match = chosen
                self._emit(self.buffer[:match.start()])
                if custom and self.rules.keep_custom:
                    self._emit(self.buffer[match.start():match.end()], delimiter=True)
                self.boundary = self.has_text
                self.buffer = self.buffer[match.end():]
            else:
                self._emit(self.buffer[:safe])
                self.buffer = self.buffer[safe:]
                break

    def _plain(self, text):
        self.buffer += text
        self._drain()

    def _protected(self, text):
        self._drain(final=True)
        self._emit(text)

    def _consume(self, char):
        if self.fence:
            if char == '\n':
                match = _FENCE.fullmatch(self.code_line.rstrip('\r'))
                closed = bool(match and match[1][0] == self.fence[0] and len(match[1]) >= self.fence[1] and not match[2].strip())
                self.code_line = ''
                if closed:
                    self.fence = None
                    self.normal_line = False
                    self._plain(char)
                else:
                    self._protected(char)
            else:
                self.code_line += char
                self._protected(char)
            return

        if self.normal_line:
            self._plain(char)
            if char == '\n':
                self.normal_line = False
            return

        self.candidate += char
        if char == '\n':
            match = _FENCE.fullmatch(self.candidate[:-1].rstrip('\r'))
            if match and not (match[1][0] == '`' and '`' in match[2]):
                self.fence = (match[1][0], len(match[1]))
                self._protected(self.candidate)
            else:
                self._plain(self.candidate)
            self.candidate = ''
            return

        # Only a possible fence opener delays ordinary text. Once three
        # markers are present, keep its header protected until newline/EOF.
        stripped = self.candidate.lstrip(' ')
        indentation = len(self.candidate) - len(stripped)
        possible = indentation <= 3 and (not stripped or re.match(r'(`{1,2}|~{1,2})$', stripped) or re.match(r'(`{3,}|~{3,})', stripped))
        if not possible:
            self._plain(self.candidate)
            self.candidate = ''
            self.normal_line = True

    def feed(self, text):
        self.output = []
        for char in text:
            self._consume(char)
        result, self.output = self.output, []
        return result

    def flush(self):
        self.output = []
        if self.candidate:
            match = _FENCE.fullmatch(self.candidate.rstrip('\r'))
            if match and not (match[1][0] == '`' and '`' in match[2]):
                self._protected(self.candidate)
            else:
                self.buffer += self.candidate
            self.candidate = ''
        self._drain(final=True)
        if self.leading_delimiters.strip():
            leading = self.leading_delimiters
            self.leading_delimiters = ''
            self._emit(leading)
        result, self.output = self.output, []
        return result

    def passthrough(self, text):
        """Keep mixed chains intact while preserving their fence context."""
        prefix = self.leading_delimiters + self.buffer + self.candidate
        self.feed(text)
        self.buffer = ''
        if self.candidate:
            self.candidate = ''
            self.normal_line = True
        self.boundary = False
        self.leading_delimiters = ''
        self.has_text = self.has_text or bool((prefix + text).strip())
        return prefix
