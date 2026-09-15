import re
import unittest
from unittest.mock import patch
import builtins
from separators import compile_advanced, decode_separator, separator_rules


class RuleTests(unittest.TestCase):
    def test_literals_are_escaped_and_unicode_preserved(self):
        rules = separator_rules({'custom_separators': ['中文', '.', r'\n', r'\r\n', r'\\n']})
        self.assertEqual(rules.literal_pattern.match('中文').group(), '中文')
        self.assertIsNone(rules.literal_pattern.match('x'))
        self.assertEqual(rules.literal_pattern.match('\r\n').group(), '\r\n')
        self.assertEqual(decode_separator(r'中文\t\\n'), '中文\t\\n')

    def test_regex_groups_keep_full_match(self):
        pattern, width = compile_advanced(r'(---)|(===)')
        self.assertEqual(width, 3)
        self.assertEqual(pattern.search('x===y').span(), (1, 4))
        self.assertEqual(compile_advanced(r'[。！？]{1,3}')[1], 3)

    def test_unsupported_patterns_are_ignored(self):
        for pattern in ['[', 'a*', 'a+', 'a?', '^a', 'a$', '(?=a)', '(?<=a)b', r'(a)\1', '(a{1,2}){1,2}', '(a|bb){1,2}', 'x{65}', 'a?' * 20 + 'b', '(a|aa){31}b']:
            messages = []
            rules = separator_rules({'advanced_separator_regex': pattern}, messages.append)
            self.assertIsNone(rules.advanced_pattern, pattern)
            self.assertTrue(rules.blank_lines)
            self.assertTrue(messages, pattern)

    def test_unavailable_regex_parser_keeps_basic_rules(self):
        real_import = builtins.__import__
        def import_without_parser(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 're' and '_parser' in fromlist:
                raise ImportError('not available')
            return real_import(name, globals, locals, fromlist, level)
        messages = []
        with patch('builtins.__import__', import_without_parser):
            rules = separator_rules({'custom_separators': ['!'], 'advanced_separator_regex': '---'}, messages.append)
        self.assertIsNone(rules.advanced_pattern)
        self.assertIsNotNone(rules.literal_pattern.match('!'))
        self.assertTrue(rules.blank_lines)
        self.assertTrue(messages)


if __name__ == '__main__':
    unittest.main()
