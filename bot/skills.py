"""Dizionario delle skill: nome canonico → sinonimi cercati nel testo degli annunci.

I sinonimi sono confrontati sul testo minuscolo e senza accenti, con confini di
parola. Le skill aggiunte dall'utente che non sono qui vengono cercate letteralmente.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, Iterable, List, Set

from .models import fold

SKILLS: Dict[str, List[str]] = {
    # Linguaggi
    "SQL": ["sql", "t-sql", "tsql", "pl/sql", "plsql", "ansi sql"],
    "Python": ["python", "pyspark", "pandas"],
    "Scala": ["scala"],
    "Java": ["java"],
    "Bash": ["bash", "shell scripting", "shell script"],
    "R": ["r studio", "rstudio", "linguaggio r", "r language"],
    # Modellazione e concetti
    "Data modeling": ["data modeling", "data modelling", "modellazione dati", "modellazione dei dati",
                      "dimensional model", "star schema", "kimball", "data vault"],
    "Data warehouse": ["data warehouse", "data warehousing", "dwh", "datawarehouse", "edw"],
    "ETL/ELT": ["etl", "elt", "data pipeline", "data pipelines", "pipeline di dati", "pipeline dati"],
    "Data lake": ["data lake", "datalake", "lakehouse", "delta lake", "iceberg", "hudi"],
    "Streaming": ["streaming", "real-time data", "real time data", "event streaming"],
    "Data quality": ["data quality", "great expectations", "data observability", "monte carlo"],
    "Data governance": ["data governance", "data catalog", "data lineage", "collibra", "datahub"],
    # Database
    "Vertica": ["vertica"],
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql", "mariadb"],
    "Oracle": ["oracle db", "oracle database", "oracle"],
    "SQL Server": ["sql server", "mssql", "ms sql"],
    "MongoDB": ["mongodb", "mongo"],
    "Cassandra": ["cassandra"],
    "Elasticsearch": ["elasticsearch", "opensearch"],
    "ClickHouse": ["clickhouse"],
    "Teradata": ["teradata"],
    # Cloud warehouse e piattaforme
    "BigQuery": ["bigquery", "big query"],
    "Snowflake": ["snowflake"],
    "Redshift": ["redshift"],
    "Databricks": ["databricks"],
    "Synapse": ["synapse"],
    "Microsoft Fabric": ["microsoft fabric", "ms fabric", "fabric"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "S3": ["s3"],
    "AWS Glue": ["aws glue", "glue"],
    "Dataflow": ["dataflow", "apache beam", "beam"],
    "Pub/Sub": ["pub/sub", "pubsub"],
    "Cloud Composer": ["cloud composer"],
    # Elaborazione e orchestrazione
    "dbt": ["dbt", "data build tool"],
    "Spark": ["spark", "pyspark", "spark sql"],
    "Hadoop": ["hadoop", "hdfs", "mapreduce"],
    "Hive": ["hive"],
    "Kafka": ["kafka", "confluent"],
    "Flink": ["flink"],
    "Airflow": ["airflow", "cloud composer"],
    "Dagster": ["dagster"],
    "Prefect": ["prefect"],
    "Airbyte": ["airbyte"],
    "Fivetran": ["fivetran"],
    "NiFi": ["nifi"],
    "Talend": ["talend"],
    "Informatica": ["informatica", "powercenter"],
    "SSIS": ["ssis"],
    "Azure Data Factory": ["azure data factory", "data factory", "adf"],
    # BI
    "Qlik": ["qlik", "qlik sense", "qlikview"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Looker": ["looker", "lookml", "looker studio"],
    "Excel": ["excel", "ms excel"],
    # DevOps
    "Git": ["git", "github", "gitlab", "bitbucket"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "github actions", "gitlab ci", "jenkins"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s", "gke", "eks", "aks"],
    "Terraform": ["terraform", "infrastructure as code", "iac"],
    "Linux": ["linux", "unix"],
    "REST API": ["rest api", "restful", "api rest"],
    # ML / AI
    "Machine learning": ["machine learning", "ml models", "scikit-learn", "sklearn"],
    "MLOps": ["mlops", "mlflow", "vertex ai", "sagemaker"],
    "LLM/GenAI": ["llm", "llms", "genai", "generative ai", "ai generativa", "openai", "claude",
                  "gemini", "langchain", "rag"],
    # Metodo
    "Agile": ["agile", "scrum", "kanban"],
}


def _pattern(alias: str) -> str:
    return r"(?<![a-z0-9])" + re.escape(fold(alias)) + r"(?![a-z0-9])"


@lru_cache(maxsize=None)
def _compiled(name: str, aliases: tuple) -> re.Pattern:
    return re.compile("|".join(_pattern(a) for a in aliases))


def canonical(name: str) -> str:
    """'bigquery' → 'BigQuery'; nomi sconosciuti restano come scritti."""
    f = fold(name).strip()
    for canon, aliases in SKILLS.items():
        if f == fold(canon) or f in (fold(a) for a in aliases):
            return canon
    return name.strip()


def aliases_for(name: str) -> tuple:
    canon = canonical(name)
    return tuple(SKILLS.get(canon, [canon]))


def extract_skills(text: str, extra: Iterable[str] = ()) -> Set[str]:
    """Skill citate nel testo: tutte quelle del dizionario più quelle personali."""
    t = fold(text)
    found = set()
    names = list(SKILLS) + [canonical(e) for e in extra if canonical(e) not in SKILLS]
    for name in names:
        if _compiled(name, aliases_for(name)).search(t):
            found.add(name)
    # "Fabric" e "Glue" da soli sono ambigui: li teniamo solo se il contesto è quello giusto.
    if "Microsoft Fabric" in found and not re.search(r"(microsoft|ms|azure|power bi)", t):
        found.discard("Microsoft Fabric")
    if "AWS Glue" in found and "AWS" not in found:
        found.discard("AWS Glue")
    return found
