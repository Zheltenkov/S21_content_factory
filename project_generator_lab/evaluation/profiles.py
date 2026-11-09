"""Read every CSV row deterministically; publish source-bound profiles and explicit samples."""
import csv
import io
import json
import math
from collections import Counter

from .models import Unit

MAX_DISTINCT = 4096
MAX_MEDIAN = 100000
MAX_SAMPLES = 32


def csv_units(path: str, text: str, sha256: str) -> list[Unit]:
    stream = io.StringIO(text, newline="")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(stream, dialect)
    header = next(reader, [])
    if not header or len(header) > 200:
        raise ValueError("CSV должен содержать от 1 до 200 столбцов")
    counts: list[Counter[str]] = [Counter() for _ in header]
    truncated = [False] * len(header)
    empty = [0] * len(header)
    numeric: list[list[float]] = [[] for _ in header]
    numeric_count = [0] * len(header)
    means = [0.0] * len(header)
    minima = [math.inf] * len(header)
    maxima = [-math.inf] * len(header)
    min_rows = [0] * len(header)
    max_rows = [0] * len(header)
    examples: dict[int, list[str]] = {}
    boundary_rows: dict[tuple[int, str], tuple[int, list[str]]] = {}
    category_rows: dict[tuple[int, str], tuple[int, list[str]]] = {}
    count = malformed = 0
    for count, row in enumerate(reader, 1):
        if len(row) != len(header):
            malformed += 1
        if count <= 3:
            examples[count] = row
        if count % 1000 == 0:
            examples[count] = row
        for i, value in enumerate(row[:len(header)]):
            empty[i] += value == ""
            if value in counts[i] or len(counts[i]) < MAX_DISTINCT:
                counts[i][value] += 1
                if len(category_rows) < MAX_DISTINCT and (i, value) not in category_rows:
                    category_rows[i, value] = (count, row)
            else:
                truncated[i] = True
            try:
                number = float(value)
            except ValueError:
                continue
            if not math.isfinite(number):
                continue
            numeric_count[i] += 1
            # Weighted updates avoid overflow from summing large finite cells.
            means[i] = means[i] * ((numeric_count[i]-1)/numeric_count[i]) + number/numeric_count[i]
            if len(numeric[i]) < MAX_MEDIAN:
                numeric[i].append(number)
            if number < minima[i]:
                minima[i], min_rows[i] = number, count
                boundary_rows[i,"min"] = (count,row)
            if number > maxima[i]:
                maxima[i], max_rows[i] = number, count
                boundary_rows[i,"max"] = (count,row)
    if count:
        examples[count] = row
    for number, values in boundary_rows.values():
        examples[number] = values
    columns = []
    preferred = {1,2,3,count, *min_rows, *max_rows}
    for i, name in enumerate(header):
        distribution_complete = not truncated[i] and len(counts[i]) <= 24
        frequent = counts[i].most_common(24 if distribution_complete else 12)
        column: dict[str, object] = {"name":name,"empty_cells":empty[i],"distinct_lower_bound":len(counts[i]),
            "distinct_exact":not truncated[i],"value_counts":dict(frequent),"value_counts_complete":distribution_complete,
            "numeric_cells":numeric_count[i]}
        if numeric_count[i]:
            column.update(min=minima[i],max=maxima[i],mean=means[i])
            if numeric_count[i] <= MAX_MEDIAN:
                ordered = sorted(numeric[i])
                middle = len(ordered)//2
                column["median"] = ordered[middle] if len(ordered)%2 else ordered[middle-1]/2+ordered[middle]/2
        if distribution_complete:
            for value, _ in frequent:
                if pair := category_rows.get((i, value)):
                    preferred.add(pair[0])
                    examples[pair[0]] = pair[1]
        columns.append(column)
    # Samples supplement complete aggregates; their absence never means a value is absent from the dataset.
    priority = list(dict.fromkeys([1,count,*min_rows,*max_rows,2,3,*sorted(preferred)]))
    selected = sorted(n for n in priority if n in examples) if len(examples) <= MAX_SAMPLES else sorted([n for n in priority if n in examples][:MAX_SAMPLES])
    profile = {"source":f"file:{path}","source_sha256":sha256,"rows_read":count,"columns":columns,
        "header":header,"malformed_width_rows":malformed,"delimiter":dialect.delimiter,
        "all_rows_read":True,"sample_is_exhaustive":len(selected)==count,
        "semantics":"Пустая ячейка — не автоматически SQL NULL. Числовая сводка не определяет предметное назначение столбца."}
    samples = {"source":f"file:{path}","source_sha256":sha256,"selection":"Детерминированные примеры категорий и числовых границ; не полный набор.",
               "header":header,"rows":[{"record":n,"values":examples[n]} for n in selected]}
    return [Unit(id=f"data:{path}:profile",title=f"Профиль данных · {path}",kind="dataset_profile",part=f"file:{path}",text=json.dumps(profile,ensure_ascii=False)),
            Unit(id=f"data:{path}:sample",title=f"Примеры записей · {path}",kind="dataset_sample",part=f"file:{path}",text=json.dumps(samples,ensure_ascii=False))]
