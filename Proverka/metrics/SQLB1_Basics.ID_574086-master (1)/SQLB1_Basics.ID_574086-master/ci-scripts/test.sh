#!/bin/bash

#
# Скрипт для формирования итогового json по резултатам теста
# Берет TEST_JSON, проходит по полям number и запускает скрипт STARTUP_SCRIPT с аргументами number и name
# в ответ получает резултат теста.
# В конце делает подмену поля result на значение полученное от STARTUP_SCRIPT
#

echo -e "Run tests:\n"
set -e

# Шаблон
TEST_JSON=tests/tests.json
# Скрипт запуска тестов
STARTUP_SCRIPT=tests/startup.sh

for TEST_NUMBER in $(jq ".tests[].number" ${TEST_JSON}); do
  echo -e "-------------------------------------------------------------------------------\n"

  # Получаем имя теста
  NAME=$(jq -r ".tests[$TEST_NUMBER].name" ${TEST_JSON})
  echo -e Test number: ${TEST_NUMBER}, name: ${NAME}"\n"

  # Получаем результат теста
  OUTPUT=$(bash ${STARTUP_SCRIPT} ${NAME} ${TEST_NUMBER})
  RESULT=${OUTPUT: -1}

  echo -e Test output: ${OUTPUT}"\n"
  echo -e Test result: ${RESULT}"\n"

  # Формируем строку для подмены резултатов в шаблоне
  UPDATE=${UPDATE}"| .tests[${TEST_NUMBER}].result = ${RESULT} "

  echo -e "-------------------------------------------------------------------------------\n"
done

echo -e "\n"

echo "vvvvv"

# Формируем результат
jq ". ${UPDATE}" ${TEST_JSON}

echo "^^^^^"
