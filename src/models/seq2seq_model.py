from typing import Optional

import lightning.pytorch as pl
import torch
from lightning.pytorch.utilities.types import STEP_OUTPUT
from transformers import (
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    get_scheduler,
)

from .modules import F1Score


def str2schema(s: str, delimiters: dict) -> dict:
    """
    Converts string representation of a database schema into nested dictionary.

    Input: '(<database_name> (<table_name_1> <column_name_1> <column_name_2>) (<table_name_2> <column_name_1> <column_name_2> <column_name_3>) (<table_name_3> <column_name_1>))'

    Output: {
        "<database_name>": {
            "<table_name_1>": ["<column_name_1>", "<column_name_2>"],
            "<table_name_2>": ["<column_name_1>", "<column_name_2>", "<column_name_3>"],
            "<table_name_3>": ["<column_name_1>"]
        }
    }
    """

    initiator = delimiters["initiator"]
    separator = delimiters["separator"]
    terminator = delimiters["terminator"]
    try:
        # Remove space after delimiters for t5,
        # see https://github.com/huggingface/transformers/issues/24743
        for token in delimiters.values():
            s = s.replace(f"{token} ", f"{token}")

        schema = {}
        trimmed_str = s[len(initiator) : -len(terminator)]
        database, tables = trimmed_str.split(separator, 1)
        tables = tables[len(initiator) : -len(terminator)]
        tables = tables.split(f"{terminator}{separator}{initiator}")
        schema[database] = {}
        for table in tables:
            table_name, *columns = table.split(separator)
            schema[database][table_name] = columns

        return schema

    except Exception:
        return {}


class Seq2SeqModel(pl.LightningModule):
    def __init__(
        self,
        model_name_or_path: str,
        generator_config: dict = {
            "max_new_tokens": 512,
            "num_beams": 1,
            "prefix_allowed_tokens_fn": None,
        },
        delimiters: dict = {
            "initiator": "<(>",
            "separator": "< >",
            "terminator": "<)>",
        },
        *,
        weight_decay: float = 0.0,
        learning_rate: float = 2e-5,
        scheduler_type: str = "linear",
        warmup_steps: int = 0,
    ) -> None:
        super().__init__()
        self.save_hyperparameters()

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name_or_path, verbose=False
        )
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name_or_path)

        num_added = self.tokenizer.add_tokens(
            list(delimiters.values()), special_tokens=True
        )
        if num_added > 0:
            self.model.resize_token_embeddings(len(self.tokenizer))

        self.collate_fn = DataCollatorForSeq2Seq(
            tokenizer=self.tokenizer, model=self.model
        )

        self.metrics = torch.nn.ModuleDict()
        for step in ["val", "test"]:
            self.metrics[step] = torch.nn.ModuleDict(
                {
                    f"{step}/database_f1": F1Score(),
                    f"{step}/table_f1": F1Score(),
                    f"{step}/column_f1": F1Score(),
                }
            )

    def forward(self, **inputs):
        return self.model(**inputs)

    def common_step(self, batch):
        outputs = self(**batch)
        loss = outputs.loss
        return loss

    def training_step(self, batch, batch_idx: int) -> STEP_OUTPUT:
        loss = self.common_step(batch)
        self.log("train/loss", loss, prog_bar=True)

        return loss

    def validation_step(self, batch, batch_idx: int) -> Optional[STEP_OUTPUT]:
        batch.pop("decoder_input_ids", None)

        outputs = self.model.generate(**batch, **self.hparams.generator_config)
        pred_texts = [
            self.postprocess_text(s)
            for s in self.tokenizer.batch_decode(
                outputs, skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
        ]
        pred_schemas = [str2schema(s, self.hparams.delimiters) for s in pred_texts]

        labels = torch.where(
            batch["labels"] != -100, batch["labels"], self.tokenizer.pad_token_id
        )
        target_texts = [
            self.postprocess_text(s)
            for s in self.tokenizer.batch_decode(
                labels, skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
        ]
        target_schemas = [str2schema(s, self.hparams.delimiters) for s in target_texts]

        self.update_metrics(pred_schemas, target_schemas, step="val")
        self.log_dict(self.metrics["val"], prog_bar=True)

        loss = self.common_step(batch)
        self.log("val/loss", loss, prog_bar=True)

        return loss

    def test_step(self, batch, batch_idx: int) -> Optional[STEP_OUTPUT]:
        batch.pop("decoder_input_ids", None)

        outputs = self.model.generate(**batch, **self.hparams.generator_config)
        pred_texts = [
            self.postprocess_text(s)
            for s in self.tokenizer.batch_decode(
                outputs, skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
        ]
        pred_schemas = [str2schema(s, self.hparams.delimiters) for s in pred_texts]

        labels = torch.where(
            batch["labels"] != -100, batch["labels"], self.tokenizer.pad_token_id
        )
        target_texts = [
            self.postprocess_text(s)
            for s in self.tokenizer.batch_decode(
                labels, skip_special_tokens=False, clean_up_tokenization_spaces=False
            )
        ]
        target_schemas = [str2schema(s, self.hparams.delimiters) for s in target_texts]

        self.update_metrics(pred_schemas, target_schemas, step="test")
        self.log_dict(self.metrics["test"], prog_bar=True)

        loss = self.common_step(batch)
        self.log("test/loss", loss, prog_bar=True)

        return loss

    def update_metrics(
        self, pred_schemas: list[dict], target_schemas: list[dict], step: str
    ):
        pred_databases = [[d for d in s] for s in pred_schemas]
        target_databases = [[d for d in s] for s in target_schemas]
        self.metrics[step][f"{step}/database_f1"](pred_databases, target_databases)

        pred_tables = [[f"{d}.{t}" for d in s for t in s[d]] for s in pred_schemas]
        target_tables = [[f"{d}.{t}" for d in s for t in s[d]] for s in target_schemas]
        self.metrics[step][f"{step}/table_f1"](pred_tables, target_tables)

        pred_columns = [
            [f"{d}.{t}.{c}" for d in s for t in s[d] for c in s[d][t]]
            for s in pred_schemas
        ]
        target_columns = [
            [f"{d}.{t}.{c}" for d in s for t in s[d] for c in s[d][t]]
            for s in target_schemas
        ]
        self.metrics[step][f"{step}/column_f1"](pred_columns, target_columns)

    def postprocess_text(self, s: str) -> str:
        for token in ["bos_token", "pad_token", "eos_token"]:
            if getattr(self.tokenizer, token) is not None:
                s = s.replace(getattr(self.tokenizer, token), "")

        return s.strip()

    def configure_optimizers(self):
        no_decay = ["bias", "LayerNorm.weight"]
        optimizer_grouped_parameters = [
            {
                "params": [
                    p
                    for n, p in self.named_parameters()
                    if not any(nd in n for nd in no_decay)
                ],
                "weight_decay": self.hparams.weight_decay,
            },
            {
                "params": [
                    p
                    for n, p in self.named_parameters()
                    if any(nd in n for nd in no_decay)
                ],
                "weight_decay": 0.0,
            },
        ]
        optimizer = torch.optim.AdamW(
            optimizer_grouped_parameters,
            lr=self.hparams.learning_rate,
        )

        scheduler = get_scheduler(
            self.hparams.scheduler_type,
            optimizer,
            num_warmup_steps=self.hparams.warmup_steps,
            num_training_steps=self.trainer.estimated_stepping_batches,
        )
        scheduler = {"scheduler": scheduler, "interval": "step", "frequency": 1}

        return [optimizer], [scheduler]
