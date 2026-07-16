def test_content_documents_validate(content):
    content.validate()


def test_catalog_has_starter_armor(catalog):
    assert "0" in catalog.armors

