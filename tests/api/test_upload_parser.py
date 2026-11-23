import io

import pytest
from starlette.datastructures import UploadFile

from study_companion.api.uploads import raw_tables_from_uploads
from study_companion.contracts import DomainError


def uploads(content: bytes):
    return [
        UploadFile(file=io.BytesIO(content), filename="input.csv") for _ in range(6)
    ]


@pytest.mark.anyio
async def test_csv_preserves_chinese_and_multiline_values():
    data = '\ufeff编号,说明\n一,"第一行\n第二行"\n'.encode()
    tables = await raw_tables_from_uploads(*uploads(data))
    assert tables.records == [
        {"编号": "一", "说明": "第一行\n第二行", "source_row_number": "1"}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("data", [b"", b"a,a\n1,2\n", b"a\n1,2\n", b"\xff"])
async def test_rejects_invalid_csv(data):
    with pytest.raises(DomainError) as error:
        await raw_tables_from_uploads(*uploads(data))
    assert error.value.code == "UNPROCESSABLE"


@pytest.fixture
def anyio_backend():
    return "asyncio"
