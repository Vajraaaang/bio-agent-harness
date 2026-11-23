"""Unknown requested axes/headers cannot be reported as complete retrieval."""
from devbench.analysis.workbench_descriptions import make_description


def test_counted_table_rows_do_not_complete_unknown_gene_dimensions():
    result = make_description({'file_type': 'csv', 'cells': None, 'genes': None,
                               'metadata_rows': 12, 'columns': ['id', 'value'],
                               'header_status': 'inspected'}, ['dimensions'], filename='table.csv')
    assert result['completion'] == 'partial'
    assert result['tables'][0]['rows'][-1] == ['Metadata rows', 12]


def test_skipped_json_header_does_not_complete_column_retrieval():
    result = make_description({'file_type': 'json', 'top_level_keys': [],
                               'header_status': 'not_inspected_byte_budget'}, ['columns'], filename='large.json')
    assert result['completion'] == 'unavailable'


def test_missing_columns_in_mixed_description_remain_partial():
    result = make_description({'file_type': 'csv', 'metadata_rows': 12,
                               'columns': [], 'header_status': 'not_inspected_byte_budget'},
                              ['columns', 'dimensions'], filename='table.csv')
    assert result['completion'] == 'partial'
