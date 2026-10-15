import unittest

from nexustrade.source_tables import read_source_tables, select_source_table_cell, SourceSelectionError


class SourceTableTests(unittest.TestCase):
    def test_distinct_equal_cells_spans_and_inline_fact_identity_are_retained(self):
        html = '<table><tr><th rowspan="2">Measure</th><th colspan="2">Fiscal years</th></tr><tr><th>2023</th><th>2024</th></tr>' \
               '<tr><td>Non-marketable securities</td><td><ix:nonfraction name="x:Investment" contextRef="prior" unitRef="USD" scale="6">31,008</ix:nonfraction></td><td>31,008</td></tr></table>'
        document = read_source_tables(html, source_id='retained-filing')
        cells = document['tables'][0]['grid'][2]
        self.assertEqual(cells[1]['text'], cells[2]['text'])
        self.assertNotEqual(cells[1]['column'], cells[2]['column'])
        selected = select_source_table_cell(document, table=0, row=2, column=1,
            row_headers=[{'row': 2, 'column': 0, 'text': 'Non-marketable securities'}],
            column_headers=[{'row': 1, 'column': 1, 'text': '2023'}], role='declared investment role',
            rationale='Inspected this statement and its classification note', fact_attributes={'contextRef': 'prior'})
        self.assertEqual(selected['cell']['facts'][0]['attributes']['scale'], '6')
        self.assertEqual(selected['status'], 'selected_source_evidence')
        with self.assertRaisesRegex(SourceSelectionError, 'selector miss is not absent'):
            select_source_table_cell(document, table=0, row=2, column=1,
                row_headers=[{'row': 2, 'column': 0, 'text': 'Total non-marketable securities'}],
                column_headers=[{'row': 1, 'column': 1, 'text': '2023'}], role='investment', rationale='reviewed')

    def test_wrong_column_or_context_fails_instead_of_silently_skipping_a_period(self):
        doc = read_source_tables('<table><tr><th>Role</th><th>FY1</th></tr><tr><td>Subcomponent</td><td>0</td></tr></table>', source_id='other-company')
        args = dict(table=0, row=1, column=1, row_headers=[{'row': 1, 'column': 0, 'text': 'Subcomponent'}],
                    column_headers=[{'row': 0, 'column': 1, 'text': 'FY1'}], role='a subcomponent', rationale='not a total')
        self.assertEqual(select_source_table_cell(doc, **args)['cell']['text'], '0')
        with self.assertRaises(SourceSelectionError):
            select_source_table_cell(doc, **{**args, 'column_headers': [{'row': 0, 'column': 1, 'text': 'FY2'}]})
        with self.assertRaises(SourceSelectionError):
            select_source_table_cell(doc, **args, fact_attributes={'contextRef': 'missing'})
        with self.assertRaises(SourceSelectionError):
            select_source_table_cell(doc, **{**args, 'row_headers': []})

    def test_malformed_spans_refuse_coordinate_guessing(self):
        for html in ['<table><tr><td rowspan="3">A</td></tr></table>', '<table><tr><td>A</td>', '<table><td>A</td></table>']:
            with self.subTest(html=html), self.assertRaises(SourceSelectionError):
                read_source_tables(html, source_id='malformed')
