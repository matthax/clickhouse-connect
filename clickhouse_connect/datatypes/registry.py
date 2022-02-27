import re
import logging

from abc import ABCMeta, abstractmethod
from decimal import Decimal

from typing import Tuple, NamedTuple, Any, Type, Dict, List


class TypeDef(NamedTuple):
    size: int
    wrappers: tuple
    keys: tuple
    values: tuple


class ClickHouseType(metaclass=ABCMeta):
    __slots__ = 'wrappers', 'from_row_binary', 'name_suffix'
    _instance_cache = None

    def __init_subclass__(cls, **kwargs):
        cls._instance_cache: Dict[TypeDef, 'ClickHouseType'] = {}
        type_map[cls.__name__.upper()] = cls

    @classmethod
    def build(cls: Type['ClickHouseType'], type_def: TypeDef):
        return cls._instance_cache.setdefault(type_def, cls(type_def))

    def __init__(self, type_def: TypeDef):
        self.name_suffix = ''
        self.wrappers = type_def.wrappers
        if 'Nullable' in self.wrappers:
            self.from_row_binary = self._nullable_from_row_binary
        else:
            self.from_row_binary = self._from_row_binary

    @property
    def name(self):
        name = f'{self.__class__.__name__}{self.name_suffix}'
        for wrapper in self.wrappers:
            name = f'{wrapper}({name})'
        return name

    @abstractmethod
    def _from_row_binary(self, source, loc):
        pass

    def _nullable_from_row_binary(self, source, loc):
        if source[loc] == 0:
            return self._from_row_binary(source, loc + 1)
        return None, loc + 1


type_map: Dict[str, Type[ClickHouseType]] = {}
size_pattern = re.compile(r'([A-Z]+)(\d+)')


def get_from_name(name: str) -> ClickHouseType:
    base = name
    size = 0
    wrappers = []
    keys = tuple()
    values = tuple()
    arg_str = ''
    if base.upper().startswith('NULLABLE'):
        wrappers.append('Nullable')
        base = base[9:-1]
    if base.upper().startswith('LOWCARDINALITY'):
        wrappers.append('LowCardinality')
        base = base[15:-1]
    if base.upper().startswith('ENUM'):
        keys, values = _parse_enum(base)
        base = base[:base.find('(')]
    paren = base.find('(')
    if paren != -1:
        arg_str = base[paren + 1:-1]
        base = base[:paren]
    if base.upper().startswith('ARRAY'):
        values = get_from_name(arg_str),
    elif arg_str:
        values = _parse_args(arg_str)
    base = base.upper()
    match = size_pattern.match(base)
    if match:
        base = match.group(1)
        size = int(match.group(2))
    try:
        type_cls = type_map[base]
    except KeyError:
        err_str = f'Unrecognized ClickHouse type base: {base} name: {name}'
        logging.error(err_str)
        raise Exception(err_str)
    return type_cls.build(TypeDef(size, tuple(wrappers), keys, values))


def _parse_enum(name) -> Tuple[Tuple[str], Tuple[int]]:
    keys = []
    values = []
    pos = name.find('(') + 1
    escaped = False
    in_key = False
    key = ''
    value = ''
    while True:
        char = name[pos]
        pos += 1
        if in_key:
            if escaped:
                key += char
                escaped = False
            else:
                if char == "'":
                    keys.append(key)
                    key = ''
                    in_key = False
                elif char == '\\':
                    escaped = True
                else:
                    key += char
        elif char not in (' ', '='):
            if char == ',':
                values.append(int(value))
                value = ''
            elif char == ')':
                values.append(int(value))
                break
            elif char == "'":
                in_key = True
            else:
                value += char
    return tuple(keys), tuple(values)


def _parse_args(name) -> [Any]:
    values: List[Any] = []
    value = ''
    l = len(name)
    in_str = False
    escaped = False
    pos = 0

    def add_value():
        if value == 'NULL':
            values.append(None)
        elif '.' in value:
            values.append(Decimal(value))
        else:
            values.append(int(value))

    while pos < l:
        char = name[pos]
        pos += 1
        if in_str:
            if escaped:
                value += char
                escaped = False
            else:
                if char == "'":
                    values.append(value)
                    value = ''
                    in_str = False
                elif char == '\\':
                    escaped = True
                else:
                    value += char
        else:
            while char == ' ':
                char = name[pos]
                pos += 1
                if pos == l:
                    break
            if char == ',':
                add_value()
                value = ''
            else:
                value += char
    if value != '':
        add_value()
    return tuple(values)
