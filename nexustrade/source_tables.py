"""Lossless table coordinates and explicit source selections, without model calls.

No row labels are mapped to accounting roles here. The caller inspects the table,
declares a role and its rationale, and checks the original row/column evidence.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence


class SourceSelectionError(ValueError):
    """A selector failed; it does not establish that a disclosure is absent."""


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self.stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == 'table':
            table: dict[str, Any] = {'index': len(self.tables), 'rows': [], 'attributes': attributes,
                                     '_row': None, '_cell': None}
            self.tables.append(table)
            self.stack.append(table)
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == 'tr':
            if table['_row'] is not None:
                raise SourceSelectionError('Unclosed table row; inspect original source instead of guessing coordinates')
            table['_row'] = []
            table['rows'].append(table['_row'])
        elif tag in {'td', 'th'}:
            if table['_row'] is None or table['_cell'] is not None:
                raise SourceSelectionError('Malformed table cell; inspect original source')
            cell = {'text': '', 'tag': tag, 'attributes': attributes, 'facts': []}
            table['_row'].append(cell)
            table['_cell'] = cell
        elif ':' in tag and table['_cell'] is not None:
            # Retain inline-XBRL context/unit/name/scale without interpreting them.
            table['_cell']['facts'].append({'tag': tag, 'attributes': attributes})
        if tag == 'br' and table['_cell'] is not None:
            table['_cell']['text'] += '\n'

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]['_cell'] is not None:
            self.stack[-1]['_cell']['text'] += data

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in {'td', 'th'}:
            table['_cell'] = None
        elif tag == 'tr':
            table['_row'] = None
        elif tag == 'table':
            self.stack.pop()


def read_source_tables(html: str | bytes, *, source_id: str) -> dict[str, Any]:
    """Preserve every table cell and its expanded row/column coordinate.

    Equal adjacent values remain separate. Spans reference their original cell;
    no numeric conversion, header inference, label alias, role choice or network.
    Input must be the retained original HTML, not extracted/reformatted prose.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError('source_id must identify the retained original')
    raw = html.encode('utf-8') if isinstance(html, str) else html
    if not isinstance(raw, bytes):
        raise TypeError('html must be text or bytes')
    parser = _Tables()
    parser.feed(raw.decode('utf-8'))
    parser.close()
    if parser.stack:
        raise SourceSelectionError('Unclosed table; source coordinates are not reliable')
    for table in parser.tables:
        grid: list[list[dict[str, Any] | None]] = [[] for _ in table['rows']]
        for row_index, row in enumerate(table['rows']):
            column = 0
            for cell in row:
                while column < len(grid[row_index]) and grid[row_index][column] is not None:
                    column += 1
                spans = [int(cell['attributes'].get(name, '1') or '1') for name in ('rowspan', 'colspan')]
                if any(not 1 <= span <= 1000 for span in spans) or row_index + spans[0] > len(grid):
                    raise SourceSelectionError('Invalid table span; inspect original source')
                cell.update(row=row_index, column=column)
                for r in range(row_index, row_index + spans[0]):
                    grid[r].extend([None] * max(0, column + spans[1] - len(grid[r])))
                    for c in range(column, column + spans[1]):
                        if grid[r][c] is not None:
                            raise SourceSelectionError('Overlapping table spans; inspect original source')
                        grid[r][c] = cell
                column += spans[1]
        table['grid'] = grid
        del table['_row'], table['_cell']
    return {'sourceId': source_id, 'sourceSha256': hashlib.sha256(raw).hexdigest(), 'tables': parser.tables}


def select_source_table_cell(document: Mapping[str, Any], *, table: int, row: int, column: int,
                             row_headers: Sequence[Mapping[str, Any]], column_headers: Sequence[Mapping[str, Any]],
                             role: str, rationale: str, fact_attributes: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Select one reviewed cell, asserting exact label/header/context evidence.

    Header selectors are {row, column, text}; whitespace alone is normalized.
    Failed, empty or ambiguous selectors raise SourceSelectionError. They must
    be repaired from the retained table/SEC role candidates, never treated as
    absent data or used to skip the dependent period. This is evidence selection,
    not economic acceptance; preserve the result for independent semantic review.
    """
    if not role.strip() or not rationale.strip() or not row_headers or not column_headers:
        raise SourceSelectionError('Declare role/rationale and exact row and column header selectors')
    try:
        selected_table = document['tables'][table]
    except (KeyError, IndexError, TypeError):
        raise SourceSelectionError(f'Table {table} not found; inspect read_source_tables output') from None
    if type(table) is not int or table < 0:
        raise SourceSelectionError('Table coordinate must be a nonnegative integer')

    def at(r: int, c: int) -> Mapping[str, Any]:
        if type(r) is not int or type(c) is not int or r < 0 or c < 0:
            raise SourceSelectionError('Cell coordinates must be nonnegative integers')
        try:
            cell = selected_table['grid'][r][c]
        except (IndexError, TypeError):
            raise SourceSelectionError(f'Cell ({r}, {c}) not found in table {table}') from None
        if cell is None:
            raise SourceSelectionError(f'Cell ({r}, {c}) is empty in table {table}')
        return cell

    def checked(selectors: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        cells = []
        for selector in selectors:
            cell = at(selector['row'], selector['column'])
            expected = selector.get('text')
            if not isinstance(expected, str) or not expected.strip() or ' '.join(cell['text'].split()) != ' '.join(expected.split()):
                raise SourceSelectionError(f"Header mismatch in table {table} at ({selector['row']}, {selector['column']}): "
                                           f"expected {expected!r}, found {cell['text']!r}. A selector miss is not absent disclosure.")
            cells.append(cell)
        return cells

    cell = at(row, column)
    if not cell['text'].strip():
        raise SourceSelectionError('Selected source cell has no disclosed text')
    headers = {'rowHeaders': checked(row_headers), 'columnHeaders': checked(column_headers)}
    if any(not header['row'] <= row < header['row'] + int(header['attributes'].get('rowspan', '1') or '1')
           for header in headers['rowHeaders']):
        raise SourceSelectionError('Row header does not cover the selected row')
    if any(not header['column'] <= column < header['column'] + int(header['attributes'].get('colspan', '1') or '1')
           or header['row'] > row for header in headers['columnHeaders']):
        raise SourceSelectionError('Column header does not cover the selected column')
    if len(cell['facts']) > 1 and fact_attributes is None:
        raise SourceSelectionError('Multiple inline facts in this cell; select the exact fact context')
    if fact_attributes is not None:
        matches = [fact for fact in cell['facts'] if all(fact['attributes'].get(k.lower()) == v for k, v in fact_attributes.items())]
        if len(matches) != 1:
            raise SourceSelectionError('Inline fact context is missing or ambiguous; inspect original cell facts')
    return {'sourceId': document['sourceId'], 'sourceSha256': document['sourceSha256'],
            'table': table, 'row': row, 'column': column, 'cell': cell, **headers,
            'role': role, 'rationale': rationale, 'status': 'selected_source_evidence'}
