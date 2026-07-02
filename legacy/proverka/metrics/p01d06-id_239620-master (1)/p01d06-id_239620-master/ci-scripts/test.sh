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
CPPCHECK_SCRIPT=tests/cppcheck_test.sh
STYLE_TEST_SCRIPT=tests/style_tests.sh
LAST_TEST_NAME=""

for TEST_NUMBER in $(jq ".tests[].number" ${TEST_JSON}); do
  # Получаем имя теста
  NAME=$(jq -r ".tests[$TEST_NUMBER].name" ${TEST_JSON})
  echo -e "-------------------------------------------------------------------------------\n"
  echo -e Test number: ${TEST_NUMBER}, name: ${NAME}"\n"
  OUTPUT=$(bash ${STYLE_TEST_SCRIPT} ${NAME})
  echo -e ${OUTPUT}
  STYLE_TEST_RESULT=${OUTPUT: -1}

  # Получаем результат теста
  OUTPUT=$(bash ${CPPCHECK_SCRIPT} ${NAME})
  if [ $STYLE_TEST_RESULT -eq 1 ]
  then
    RESULT=${OUTPUT: -1}
  else
    RESULT=0
  fi
  echo -e ${OUTPUT}

  # Формируем строку для подмены резултатов в шаблоне
  UPDATE=${UPDATE}"| .tests[${TEST_NUMBER}].result = ${RESULT} "
done

echo -e "-------------------------------------------------------------------------------\n"

echo -e "\n"

echo "vvvvv"

# Формируем результат
jq ". ${UPDATE}" ${TEST_JSON}

echo "^^^^^"

