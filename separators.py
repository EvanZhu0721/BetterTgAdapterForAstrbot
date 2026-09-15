"""Validate separator rules; matching itself uses Python's regular expressions."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SeparatorRules:
    blank_lines: bool = True
    literal_pattern: object = None
    advanced_pattern: object = None
    width: int = 1
    keep_custom: bool = True


def decode_separator(value):
    return re.sub(r'\\([nrt\\])', lambda m: {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\'}[m[1]], value)


def compile_advanced(pattern):
    try:
        from re import _parser as parser
    except ImportError as exc:
        raise ValueError('当前 Python 不支持高级正则校验') from exc
    if len(pattern) > 512:
        raise ValueError('高级正则超过 512 字符')
    parsed = parser.parse(pattern, 0)
    minimum, maximum = parsed.getwidth()
    if minimum == 0 or maximum > 64:
        raise ValueError('高级正则必须消耗字符，且最大匹配长度不超过 64；不支持无界量词')

    def validate(items, repeated=False):
        cost = 1
        for opcode, value in items:
            name = str(opcode)
            item_cost = 1
            if name in {'LITERAL', 'NOT_LITERAL', 'ANY'}:
                continue
            if name == 'IN':
                if any(str(op) not in {'LITERAL', 'RANGE', 'CATEGORY', 'NEGATE'} for op, _ in value):
                    raise ValueError('不支持此字符类结构')
            elif name == 'SUBPATTERN':
                item_cost = validate(value[-1], repeated)
            elif name == 'BRANCH' and not repeated:
                item_cost = sum(validate(branch, repeated) for branch in value[1])
            elif name in {'MAX_REPEAT', 'MIN_REPEAT'} and not repeated:
                if value[1] > 64:
                    raise ValueError('不支持无界或过大的重复')
                child_cost = validate(value[2], True)
                item_cost = 0
                for count in range(value[0], value[1] + 1):
                    item_cost += min(child_cost ** count, 4097)
                    if item_cost > 4096:
                        break
            else:
                raise ValueError('不支持锚点、断言、反向引用、嵌套重复或重复内分支')
            cost *= item_cost
            if cost > 4096:
                raise ValueError('高级正则分支复杂度过高')
        return cost

    validate(parsed)
    return re.compile(pattern), maximum


def separator_rules(config=None, warn=None):
    config = config or {}
    warn = warn or (lambda message: None)
    width = 1
    literals = []
    values = config.get('custom_separators', [])
    if not isinstance(values, list):
        warn('自定义分隔符必须是列表，已忽略。')
        values = []
    for index, value in enumerate(values):
        if index >= 32:
            warn('最多支持 32 个自定义分隔符，已忽略其余项。')
            break
        if not isinstance(value, str):
            warn(f'已忽略第 {index + 1} 个分隔符：必须是文本。')
            continue
        value = decode_separator(value)
        if not value or len(value) > 64:
            warn(f'已忽略第 {index + 1} 个分隔符：长度须为 1–64 字符。')
            continue
        literals.append(value)
        width = max(width, len(value))
    literal_pattern = re.compile('|'.join(re.escape(item) for item in sorted(set(literals), key=lambda item: (-len(item), item)))) if literals else None
    advanced = config.get('advanced_separator_regex', '')
    advanced_pattern = None
    if advanced:
        try:
            if not isinstance(advanced, str):
                raise ValueError('高级正则必须是文本')
            advanced_pattern, advanced_width = compile_advanced(advanced)
            width = max(width, advanced_width)
        except (ValueError, re.error, AttributeError, OverflowError, RecursionError) as exc:
            warn(f'已忽略无效高级分隔正则：{exc}')
    return SeparatorRules(config.get('split_on_blank_lines', True) is not False, literal_pattern, advanced_pattern, width, config.get('keep_custom_separators', True) is not False)
