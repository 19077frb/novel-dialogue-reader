"""Keep the project's temporary-data retention policy explicit."""


def test_temporary_data_retention_policy(pytestconfig):
    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
    assert int(pytestconfig.getini("tmp_path_retention_count")) == 3
