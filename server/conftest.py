"""Run legacy script modules once through test_all; collect native pytest modules normally."""


def pytest_collection_modifyitems(items):
    items[:] = [item for item in items
                if item.module.__name__ == "test_all" or not hasattr(item.module, "main")]
