import unittest
from separators import separator_rules
from splitter import ParagraphSplitter


def split(chunks, config):
    parser = ParagraphSplitter(separator_rules(config))
    parts = ['']
    def accept(tokens):
        for token in tokens:
            if token is None:
                parts.append('')
            else:
                parts[-1] += token
    for chunk in chunks:
        accept(parser.feed(chunk))
    accept(parser.flush())
    return parts


class CustomSplitterTests(unittest.TestCase):
    def check_chunks(self, text, config, expected):
        self.assertEqual(split([text], config), expected)
        self.assertEqual(split(list(text), config), expected)
        for index in range(len(text) + 1):
            self.assertEqual(split([text[:index], text[index:]], config), expected, index)

    def test_disable_builtin_and_add_single_newline(self):
        self.check_chunks('First\n\nSecond', {'split_on_blank_lines': False}, ['First\n\nSecond'])
        self.check_chunks('First\nSecond', {'split_on_blank_lines': False, 'custom_separators': [r'\n'], 'keep_custom_separators': False}, ['First', 'Second'])

    def test_overlapping_literals_longest_and_eof(self):
        config = {'custom_separators': ['|', '||']}
        self.check_chunks('a||b|c|', config, ['a||', 'b|', 'c|'])
        self.check_chunks('a||b|c|', dict(config, keep_custom_separators=False), ['a', 'b', 'c'])

    def test_unicode_and_regex_metacharacters_are_literal(self):
        self.check_chunks('甲中文乙.丙', {'custom_separators': ['中文', '.']}, ['甲中文', '乙.', '丙'])

    def test_raw_regex_capture_span_and_greedy_bound(self):
        self.check_chunks('甲？！乙。尾', {'advanced_separator_regex': '[。！？]{1,3}'}, ['甲？！', '乙。', '尾'])
        self.check_chunks('a---b===c', {'advanced_separator_regex': '(---)|(===)', 'keep_custom_separators': False}, ['a', 'b', 'c'])

    def test_code_headers_and_body_protected_even_when_markers_are_separators(self):
        code = '```python\na|b\n\nc\n```'
        self.check_chunks('Before\n\n' + code + '\n\nAfter', {'custom_separators': ['```', 'python', '|']}, ['Before', code, 'After'])
        code = '~~~python\na|b\n\nc\n~~~'
        self.check_chunks(code + '\nAfter', {'custom_separators': [r'\n', '~~~', 'python', '|'], 'keep_custom_separators': False}, [code, 'After'])

    def test_no_empty_message_for_trailing_or_consecutive_delimiters(self):
        self.check_chunks('a\n\n\nb\n', {'split_on_blank_lines': False, 'custom_separators': [r'\n'], 'keep_custom_separators': False}, ['a', 'b'])
        self.check_chunks('Hello!!World', {'custom_separators': ['!']}, ['Hello!!', 'World'])
        self.check_chunks('!!Hello!!', {'custom_separators': ['!']}, ['!!Hello!!'])

    def test_bounded_regex_tail_is_released_at_eof(self):
        self.check_chunks('a====b', {'advanced_separator_regex': '={2,4}'}, ['a====', 'b'])
        self.check_chunks('a=', {'advanced_separator_regex': '={2,4}'}, ['a='])

    def test_invalid_regex_does_not_disable_builtin(self):
        self.check_chunks('a\n\nb', {'advanced_separator_regex': '('}, ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
