import json
import sys
from pathlib import Path

import wordninja
from sqlglot import exp, parse_one
from tqdm import tqdm

sys.path.append(str(Path(__file__).parents[1]))

RAW_DATA_PATH = Path("./data/raw")
TGT_PATH = Path("./data/")


def load_data(file_path: Path | list[Path]) -> list[dict]:
    data = []
    file_path = [file_path] if isinstance(file_path, Path) else file_path
    for f in file_path:
        with f.open() as f:
            data.extend(json.load(f))

    return data


def write_data(file_path: Path, data: list[dict] | dict):
    with file_path.open("w") as f:
        json.dump(data, f, indent=2)


def extract_metadata(sql_query: str, database: list[dict]) -> list[dict]:
    """
    Given a SQL query and a database schema, extract metadata from the query.
    """
    parsed_query = parse_one(sql_query, read="sqlite")

    tables = [t.name.lower() for t in parsed_query.find_all(exp.Table)]
    columns = [c.name.lower() for c in parsed_query.find_all(exp.Column)]

    metadata = [
        {
            "name": t["name"],
            "columns": [
                c["name"] for c in t["columns"] if c["original_name"].lower() in columns
            ],
        }
        for t in database
        if t["original_name"].lower() in tables
    ]

    return metadata


def get_dataset_schemas(dataset) -> dict:
    type_offset = 0
    if dataset == "spider":
        with (RAW_DATA_PATH / dataset / "tables.json").open(mode="r") as file:
            raw_tables = json.load(file)
    elif dataset == "bird":
        with (RAW_DATA_PATH / dataset / "train" / "train_tables.json").open() as f:
            raw_tables = json.load(f)
        with (RAW_DATA_PATH / dataset / "dev" / "dev_tables.json").open() as f:
            raw_tables.extend(json.load(f))
        type_offset = 1
    elif dataset in ["fiben", "wikisql"]:
        data_path = RAW_DATA_PATH / f"unified-text2sql-benchmark/unified/{dataset}"
        with (data_path / "tables.json").open(mode="r") as file:
            raw_tables = json.load(file)
    else:
        raise ValueError(f"Unknown dataset: {dataset}")

    databases = {}
    for db in raw_tables:
        tables = []
        for i, t in enumerate(db["table_names"]):
            db["table_names"][i] = t.lower()
            if dataset == "fiben":
                db["table_names"][i] = " ".join(wordninja.split(t))
        for i, (_, t) in enumerate(db["column_names"]):
            db["column_names"][i][1] = t.lower()
            if dataset == "fiben":
                db["column_names"][i][1] = " ".join(wordninja.split(t))

        for i in range(len(db["table_names"])):
            table = {}
            table["name"] = db["table_names"][i]
            table["original_name"] = db["table_names_original"][i]
            table["columns"] = []
            for j in range(len(db["column_names"])):
                if db["column_names"][j][0] == i:
                    column = {}
                    column["name"] = db["column_names"][j][1]
                    column["original_name"] = db["column_names_original"][j][1]
                    column["type"] = db["column_types"][j - type_offset]
                    if j in db["primary_keys"]:
                        column["primary_key"] = True

                    for left, right in db["foreign_keys"]:
                        if left == j:
                            column["foreign_key"] = {
                                "table": db["table_names"][
                                    db["column_names"][right][0]
                                ],
                                "column": db["column_names"][right][1],
                            }
                    table["columns"].append(column)
            tables.append(table)

        databases[db["db_id"]] = tables
    return databases


def spider(ds_path=RAW_DATA_PATH / "spider", tgt_path=TGT_PATH / "spider"):
    databases = get_dataset_schemas("spider")
    tgt_path.mkdir(exist_ok=True, parents=True)
    write_data(tgt_path / "schemas.json", databases)

    def convert_data(data):
        for record in tqdm(data):
            metadata = extract_metadata(record["query"], databases[record["db_id"]])
            record["schema"] = {
                "database": record.pop("db_id"),
                "metadata": metadata,
            }
            record["sql"] = record.pop("query")
            for key in ["query_toks", "query_toks_no_value", "question_toks"]:
                record.pop(key, None)

    train_files = list(ds_path.glob("train_*.json"))
    train_data = load_data(train_files)
    convert_data(train_data)
    write_data(tgt_path / "train.json", train_data)

    dev_data = load_data(ds_path / "dev.json")
    convert_data(dev_data)
    write_data(tgt_path / "test.json", dev_data)


def spider_syn(ds_path=RAW_DATA_PATH / "spider-syn", tgt_path=TGT_PATH / "spider_syn"):
    databases = get_dataset_schemas("spider")
    tgt_path.mkdir(exist_ok=True, parents=True)
    write_data(tgt_path / "schemas.json", databases)

    def convert_data(data):
        for record in tqdm(data):
            metadata = extract_metadata(record["query"], databases[record["db_id"]])
            record["schema"] = {
                "database": record.pop("db_id"),
                "metadata": metadata,
            }
            record["sql"] = record.pop("query")
            record["question"] = record.pop("SpiderSynQuestion")
            for key in ["SpiderQuestion"]:
                record.pop(key, None)

    train_data = load_data(ds_path / "train_spider.json")
    convert_data(train_data)
    write_data(tgt_path / "train.json", train_data)

    dev_data = load_data(ds_path / "dev.json")
    convert_data(dev_data)
    write_data(tgt_path / "test.json", dev_data)


def spider_realistic(
    ds_path=RAW_DATA_PATH / "spider-realistic", tgt_path=TGT_PATH / "spider_realistic"
):
    databases = get_dataset_schemas("spider")
    tgt_path.mkdir(exist_ok=True, parents=True)
    write_data(tgt_path / "schemas.json", databases)

    def convert_data(data):
        for record in tqdm(data):
            metadata = extract_metadata(record["query"], databases[record["db_id"]])
            record["schema"] = {
                "database": record.pop("db_id"),
                "metadata": metadata,
            }
            record["sql"] = record.pop("query")
            for key in ["query_toks", "query_toks_no_value", "question_toks"]:
                record.pop(key, None)

    dev_data = load_data(ds_path / "spider-realistic.json")
    convert_data(dev_data)
    write_data(tgt_path / "test.json", dev_data)


def bird(ds_path=RAW_DATA_PATH / "bird", tgt_path=TGT_PATH / "bird"):
    databases = get_dataset_schemas("bird")
    tgt_path.mkdir(exist_ok=True, parents=True)
    write_data(tgt_path / "schemas.json", databases)

    def convert_data(data):
        for record in tqdm(data[:]):
            db_id = record["db_id"]
            try:
                metadata = extract_metadata(record["SQL"], databases[db_id])
                record["schema"] = {
                    "database": record.pop("db_id"),
                    "metadata": metadata,
                }
                record["sql"] = record.pop("SQL")
                for key in [
                    "question_toks",
                    "SQL_toks",
                    "evidence_toks",
                    "evidence",
                    "question_id",
                    "difficulty",
                ]:
                    record.pop(key, None)
            except Exception:
                data.remove(record)

    train_data = load_data(ds_path / "train" / "train.json")
    convert_data(train_data)
    write_data(tgt_path / "train.json", train_data)

    dev_data = load_data(ds_path / "dev" / "dev.json")
    convert_data(dev_data)
    write_data(tgt_path / "test.json", dev_data)


def fiben(
    ds_path=RAW_DATA_PATH / "unified-text2sql-benchmark/unified/fiben",
    tgt_path=TGT_PATH / "fiben",
):
    databases = get_dataset_schemas("fiben")
    tgt_path.mkdir(exist_ok=True, parents=True)
    write_data(tgt_path / "schemas.json", databases)

    def convert_data(data):
        for record in tqdm(data[:]):
            try:
                metadata = extract_metadata(record["query"], databases[record["db_id"]])
                record["schema"] = {
                    "database": record.pop("db_id"),
                    "metadata": metadata,
                }
                record["sql"] = record.pop("query")
                for key in [
                    "query_toks",
                    "query_toks_no_value",
                    "question_toks",
                ]:
                    record.pop(key, None)
                record["question"] = record["question"].strip()
                record["sql"] = record["sql"].strip()
            except Exception:
                data.remove(record)

    dev_data = []
    with (ds_path / "dev.jsonl").open() as f:
        for line in f:
            dev_data.append(json.loads(line))

    convert_data(dev_data)
    write_data(tgt_path / "test.json", dev_data)


def wikisql(
    ds_path=RAW_DATA_PATH / "unified-text2sql-benchmark/unified/wikisql",
    tgt_path=TGT_PATH / "wikisql",
):
    databases = get_dataset_schemas("wikisql")
    tgt_path.mkdir(exist_ok=True, parents=True)
    write_data(tgt_path / "schemas.json", databases)

    def convert_data(data):
        for record in tqdm(data[:]):
            try:
                metadata = extract_metadata(record["query"], databases[record["db_id"]])
                record["schema"] = {
                    "database": record.pop("db_id"),
                    "metadata": metadata,
                }
                record["sql"] = record.pop("query")
                for key in [
                    "query_toks",
                    "query_toks_no_value",
                    "question_toks",
                ]:
                    record.pop(key, None)
            except Exception:
                data.remove(record)

    train_files = [ds_path / "train.jsonl", ds_path / "dev.jsonl"]
    train_data = []
    for f in train_files:
        with f.open() as f:
            for line in f:
                train_data.append(json.loads(line))

    convert_data(train_data)
    write_data(tgt_path / "train.json", train_data)

    dev_data = []
    with (ds_path / "test.jsonl").open() as f:
        for line in f:
            dev_data.append(json.loads(line))

    convert_data(dev_data)
    write_data(tgt_path / "test.json", dev_data)


if __name__ == "__main__":
    spider()
    spider_syn()
    spider_realistic()
    bird()
    fiben()
    wikisql()
