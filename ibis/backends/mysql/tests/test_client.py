from __future__ import annotations

from datetime import date
from operator import methodcaller

import pandas as pd
import pandas.testing as tm
import pytest
import sqlglot as sg
from pytest import param

import ibis
import ibis.expr.datatypes as dt
from ibis import udf
from ibis.backends.mysql.tests.conftest import (
    IBIS_TEST_MYSQL_DB,
    MYSQL_HOST,
    MYSQL_PASS,
    MYSQL_USER,
)
from ibis.backends.tests.errors import MySQLOperationalError, MySQLProgrammingError
from ibis.util import gen_name

MYSQL_TYPES = [
    param("tinyint", dt.int8, id="tinyint"),
    param("int1", dt.int8, id="int1"),
    param("boolean", dt.int8, id="boolean"),
    param("smallint", dt.int16, id="smallint"),
    param("int2", dt.int16, id="int2"),
    param("mediumint", dt.int32, id="mediumint"),
    param("int3", dt.int32, id="int3"),
    param("int", dt.int32, id="int"),
    param("int4", dt.int32, id="int4"),
    param("integer", dt.int32, id="integer"),
    param("bigint", dt.int64, id="bigint"),
    param("decimal", dt.Decimal(10, 0), id="decimal"),
    param("decimal(5, 2)", dt.Decimal(5, 2), id="decimal_5_2"),
    param("dec", dt.Decimal(10, 0), id="dec"),
    param("numeric", dt.Decimal(10, 0), id="numeric"),
    param("fixed", dt.Decimal(10, 0), id="fixed"),
    param("float", dt.float32, id="float"),
    param("double", dt.float64, id="double"),
    param("timestamp", dt.Timestamp("UTC"), id="timestamp"),
    param("date", dt.date, id="date"),
    param("time", dt.time, id="time"),
    param("datetime", dt.timestamp, id="datetime"),
    param("year", dt.uint8, id="year"),
    param("char(32)", dt.String(length=32), id="char"),
    param("char byte", dt.binary, id="char_byte"),
    param("varchar(42)", dt.String(length=42), id="varchar"),
    param("binary(42)", dt.binary, id="binary"),
    param("varbinary(42)", dt.binary, id="varbinary"),
    param("bit(1)", dt.int8, id="bit_1"),
    param("bit(9)", dt.int16, id="bit_9"),
    param("bit(17)", dt.int32, id="bit_17"),
    param("bit(33)", dt.int64, id="bit_33"),
    # mariadb doesn't have a distinct json type
    param("set('a', 'b', 'c', 'd')", dt.Array(dt.string), id="set"),
    param("mediumblob", dt.binary, id="mediumblob"),
    param("blob", dt.binary, id="blob"),
    param("mediumint(8) unsigned", dt.uint32, id="mediumint-unsigned"),
    param("bigint unsigned", dt.uint64, id="bigint-unsigned"),
    param("int unsigned", dt.uint32, id="int-unsigned"),
    param("smallint unsigned", dt.uint16, id="smallint-unsigned"),
    param("tinyint unsigned", dt.uint8, id="tinyint-unsigned"),
] + [
    param(
        f"datetime({scale:d})",
        dt.Timestamp(scale=scale or None),
        id=f"datetime{scale:d}",
    )
    for scale in range(7)
]


@pytest.mark.parametrize(("mysql_type", "expected_type"), MYSQL_TYPES)
def test_get_schema_from_query(con, mysql_type, expected_type):
    raw_name = ibis.util.guid()
    name = sg.to_identifier(raw_name, quoted=True).sql("mysql")
    expected_schema = ibis.schema(dict(x=expected_type))

    # temporary tables get cleaned up by the db when the session ends, so we
    # don't need to explicitly drop the table
    with con.begin() as c:
        c.execute(f"CREATE TEMPORARY TABLE {name} (x {mysql_type})")

    result_schema = con._get_schema_using_query(f"SELECT * FROM {name}")
    assert result_schema == expected_schema

    t = con.table(raw_name)
    assert t.schema() == expected_schema


@pytest.mark.parametrize(
    ("mysql_type", "get_schema_expected_type", "table_expected_type"),
    [
        param("json", dt.binary, dt.string, id="json"),
        param("inet6", dt.binary, dt.inet, id="inet"),
        param("uuid", dt.binary, dt.uuid, id="uuid"),
        param(
            "enum('small', 'medium', 'large')",
            dt.String(length=6),
            dt.string,
            id="enum",
        ),
        param("mediumtext", dt.String(length=2**24 - 1), dt.string, id="mediumtext"),
        param("text", dt.String(length=2**16 - 1), dt.string, id="text"),
    ],
)
def test_get_schema_from_query_special_cases(
    con, mysql_type, get_schema_expected_type, table_expected_type
):
    raw_name = ibis.util.guid()
    name = sg.to_identifier(raw_name, quoted=True).sql("mysql")
    get_schema_expected_schema = ibis.schema(dict(x=get_schema_expected_type))
    table_expected_schema = ibis.schema(dict(x=table_expected_type))

    # temporary tables get cleaned up by the db when the session ends, so we
    # don't need to explicitly drop the table
    with con.begin() as c:
        c.execute(f"CREATE TEMPORARY TABLE {name} (x {mysql_type})")

    result_schema = con._get_schema_using_query(f"SELECT * FROM {name}")
    assert result_schema == get_schema_expected_schema

    t = con.table(raw_name)
    assert t.schema() == table_expected_schema


@pytest.mark.parametrize("coltype", ["TINYBLOB", "MEDIUMBLOB", "BLOB", "LONGBLOB"])
def test_blob_type(con, coltype):
    tmp = f"tmp_{ibis.util.guid()}"
    with con.begin() as c:
        c.execute(f"CREATE TEMPORARY TABLE {tmp} (a {coltype})")
    t = con.table(tmp)
    assert t.schema() == ibis.schema({"a": dt.binary})


@pytest.fixture(scope="session")
def tmp_t(con):
    with con.begin() as c:
        c.execute("CREATE TABLE IF NOT EXISTS test_schema.t (x INET6)")
    yield "t"
    with con.begin() as c:
        c.execute("DROP TABLE IF EXISTS test_schema.t")


def test_get_schema_from_query_other_schema(con, tmp_t):
    t = con.table(tmp_t, database="test_schema")
    assert t.schema() == ibis.schema({"x": dt.inet})


def test_zero_timestamp_data(con):
    sql = """
    CREATE TEMPORARY TABLE ztmp_date_issue
    (
        name      CHAR(10) NULL,
        tradedate DATETIME NOT NULL,
        date      DATETIME NULL
    )
    """
    with con.begin() as c:
        c.execute(sql)
        c.execute(
            """
            INSERT INTO ztmp_date_issue VALUES
                ('C', '2018-10-22', 0),
                ('B', '2017-06-07', 0),
                ('C', '2022-12-21', 0)
            """
        )
    t = con.table("ztmp_date_issue")
    result = t.execute()
    expected = pd.DataFrame(
        {
            "name": ["C", "B", "C"],
            "tradedate": pd.to_datetime(
                [date(2018, 10, 22), date(2017, 6, 7), date(2022, 12, 21)]
            ),
            "date": [pd.NaT, pd.NaT, pd.NaT],
        }
    )
    tm.assert_frame_equal(result, expected)


@pytest.fixture(scope="module")
def enum_t(con):
    name = gen_name("mysql_enum_test")
    with con.begin() as cur:
        cur.execute(
            f"CREATE TEMPORARY TABLE {name} (sml ENUM('small', 'medium', 'large'))"
        )
        cur.execute(f"INSERT INTO {name} VALUES ('small')")

    yield con.table(name)
    con.drop_table(name, force=True)


@pytest.mark.parametrize(
    ("expr_fn", "expected"),
    [
        (methodcaller("startswith", "s"), pd.Series([True], name="sml")),
        (methodcaller("endswith", "m"), pd.Series([False], name="sml")),
        (methodcaller("re_search", "mall"), pd.Series([True], name="sml")),
        (methodcaller("lstrip"), pd.Series(["small"], name="sml")),
        (methodcaller("rstrip"), pd.Series(["small"], name="sml")),
        (methodcaller("strip"), pd.Series(["small"], name="sml")),
    ],
    ids=["startswith", "endswith", "re_search", "lstrip", "rstrip", "strip"],
)
def test_enum_as_string(enum_t, expr_fn, expected):
    expr = expr_fn(enum_t.sml).name("sml")
    res = expr.execute()
    tm.assert_series_equal(res, expected)


def test_builtin_scalar_udf(con):
    @udf.scalar.builtin
    def soundex(a: str) -> str:
        """Soundex of a string."""

    expr = soundex("foo")
    result = con.execute(expr)
    assert result == "F000"


def test_builtin_agg_udf(con):
    @udf.agg.builtin
    def json_arrayagg(a) -> str:
        """Glom together some JSON."""

    ft = con.tables.functional_alltypes[:5]
    expr = json_arrayagg(ft.string_col)
    result = expr.execute()
    expected = '["0","1","2","3","4"]'
    assert result == expected


def test_list_tables(con):
    mysql_tables = {
        "column_stats",
        "columns_priv",
        "db",
        "event",
        "func",
    }
    assert con.list_tables()

    assert mysql_tables.issubset(con.list_tables(database="mysql"))
    assert mysql_tables.issubset(con.list_tables(database=("mysql",)))


def test_invalid_port():
    port = 4000
    url = f"mysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}:{port}/{IBIS_TEST_MYSQL_DB}"
    with pytest.raises(MySQLOperationalError):
        ibis.connect(url)


def test_create_database_exists(con):
    con.create_database(dbname := gen_name("dbname"))

    with pytest.raises(MySQLProgrammingError):
        con.create_database(dbname)

    con.create_database(dbname, force=True)

    con.drop_database(dbname, force=True)


def test_drop_database_exists(con):
    con.create_database(dbname := gen_name("dbname"))

    con.drop_database(dbname)

    with pytest.raises(MySQLOperationalError):
        con.drop_database(dbname)

    con.drop_database(dbname, force=True)
