import json
from pathlib import Path
from typing import Literal

from ranx import Qrels, Run, evaluate
from retriv import SparseRetriever


def generate_collection(schemas, resolution) -> dict[str, str]:
    for db, tables in schemas:
        if resolution == "column":
            for tbl in tables:
                for col in tbl["columns"]:
                    doc = {
                        "id": f"{db}.{tbl['name']}.{col}",
                        "text": f"{db} {tbl['name']} {col}",
                    }
                    yield doc
        elif resolution == "table":
            for tbl in tables:
                text = f"{db} {tbl['name']} {' '.join(tbl['columns'])}"
                doc = {"id": f"{db}.{tbl['name']}", "text": text}
                yield doc
        elif resolution == "database":
            text = " ".join(
                f"{tbl['name']} {' '.join(tbl['columns'])}" for tbl in tables
            )
            text = f"{db} {text}"
            doc = {"id": db, "text": text}
            yield doc
        else:
            raise ValueError(f"Unknown resolution: {resolution}")


def generate_qrels(instances, resolution) -> dict[str, dict]:
    if resolution.startswith("all_"):
        resolution = resolution[len("all_") :]

    qrels = {}
    for i, it in enumerate(instances):
        if resolution == "column":
            qrels[str(i)] = {
                f'{it["schema"]["database"]}.{tbl["name"]}.{col}': 1
                for tbl in it["schema"]["metadata"]
                for col in tbl["columns"]
            }
        elif resolution == "table":
            qrels[str(i)] = {
                f'{it["schema"]["database"]}.{tbl["name"]}': 1
                for tbl in it["schema"]["metadata"]
            }
        elif resolution == "database":
            qrels[str(i)] = {it["schema"]["database"]: 1}
        else:
            raise ValueError(f"Unknown resolution: {resolution}")
    return qrels


def retrieve_schemas(
    dataset: Literal["spider", "bird"],
    resolution: Literal["database", "table", "column", "all_table", "all_column"],
):
    retriever = SparseRetriever(index_name=f"data/{dataset}.index")
    with Path(f"data/{dataset}_schemas.json").open() as f:
        schemas = json.load(f)

    if "all" not in resolution:
        retriever = retriever.index(
            generate_collection(schemas.items(), resolution),
            show_progress=False,
        )

    for path in Path("data").glob(f"{dataset}_test*.json"):
        with path.open() as f:
            test = json.load(f)

        queries = [{"id": str(i), "text": it["question"]} for i, it in enumerate(test)]
        if "all" not in resolution:
            results = retriever.bsearch(queries, show_progress=False)
        elif resolution == "all_table":
            results = {
                str(i): {
                    f"{it['schema']['database']}.{tbl['name']}": 1
                    for tbl in schemas[it["schema"]["database"]]
                }
                for i, it in enumerate(test)
            }
        elif resolution == "all_column":
            results = {
                str(i): {
                    f"{it['schema']['database']}.{tbl['name']}.{col}": 1
                    for tbl in schemas[it["schema"]["database"]]
                    for col in tbl["columns"]
                }
                for i, it in enumerate(test)
            }

        qrels = Qrels(generate_qrels(test, resolution))
        run = Run(results)
        metrics = ["recall@1", "recall@5", "recall@10", "f1@10", "f1"]
        print(
            path.stem,
            resolution,
            evaluate(qrels, run, metrics=metrics),
            sep="\t",
        )


if __name__ == "__main__":
    for dataset in ["spider", "bird"]:
        for resolution in ["database", "table", "column", "all_table", "all_column"]:
            retrieve_schemas(dataset, resolution)
