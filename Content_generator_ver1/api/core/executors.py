"""Централизованные пулы потоков для выполнения задач."""

import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor

# Размеры пулов на основе CPU
CPU_COUNT = multiprocessing.cpu_count()

# Оптимизированные размеры пулов
db_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("DB_EXECUTOR_WORKERS", min(CPU_COUNT * 2, 10))),
    thread_name_prefix="db"
)
log_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("LOG_EXECUTOR_WORKERS", min(CPU_COUNT, 4))),
    thread_name_prefix="log"
)
validation_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("VALIDATION_EXECUTOR_WORKERS", min(CPU_COUNT * 2, 8))),
    thread_name_prefix="validation"
)
general_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("GENERAL_EXECUTOR_WORKERS", min(CPU_COUNT, 4))),
    thread_name_prefix="general"
)

