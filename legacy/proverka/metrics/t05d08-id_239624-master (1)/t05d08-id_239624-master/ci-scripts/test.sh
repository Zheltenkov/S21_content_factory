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
STYLE_TEST_SCRIPT=tests/style_tests.sh
BUILD_SCRIPT=ci-scripts/build.sh
LAST_TEST_NAME=""
ERROR_BUILD=""

for TEST_NUMBER in $(jq ".tests[].number" ${TEST_JSON}); do

  # Получаем имя теста
  NAME=$(jq -r ".tests[$TEST_NUMBER].name" ${TEST_JSON})
  if [ "$LAST_TEST_NAME" != "$NAME" ]
  then
    echo -e "-------------------------------------------------------------------------------\n"
    OUTPUT=$(bash ${STYLE_TEST_SCRIPT} ${NAME})
    printf "${NAME}"'\n\n'
    printf 'Style test\n'
    printf 'Style test output:\n%s\n\n' "$OUTPUT"
    LAST_TEST_NAME=$NAME
    STYLE_TEST_RESULT=${OUTPUT: -1}
    printf 'Style test result: %s\n\n' "$STYLE_TEST_RESULT"
    echo -e "-------------------------------------------------------------------------------\n"
    OUTPUT=$(bash ${BUILD_SCRIPT} ${NAME})
    printf 'Build output:\n%s\n\n' "$OUTPUT"
    BUILD_RESULT=${OUTPUT: -1}
    printf 'Build result: %s\n\n' "$BUILD_RESULT"
    ERROR_BUILD=0
  fi

  if [ $BUILD_RESULT -eq 1 ]
  then
    echo -e "-------------------------------------------------------------------------------\n"
    echo -e Test number: ${TEST_NUMBER}, name: ${NAME}"\n"


    # Получаем результат теста
    OUTPUT=$(bash ${STARTUP_SCRIPT} ${NAME} ${TEST_NUMBER})
    if [ $STYLE_TEST_RESULT -eq 1 ]
    then
        RESULT=${OUTPUT: -1}
    else
        RESULT=0
    fi

    printf 'Test output: %s\n\n' "$OUTPUT"
    printf 'Test result: %s\n\n' "$RESULT"
  else
    if [ $ERROR_BUILD -eq 0 ]
    then
        echo -e "-------------------------------------------------------------------------------\n"
        echo -e Test output: Build fail"\n"
        echo -e Test result: 0"\n"
        RESULT=0
        ERROR_BUILD=1
     fi
  fi

  # Формируем строку для подмены резултатов в шаблоне
  UPDATE=${UPDATE}"| .tests[${TEST_NUMBER}].result = ${RESULT} "
done

echo -e "-------------------------------------------------------------------------------\n"

echo -e "\n"

echo "vvvvv"

# Формируем результат
jq ". ${UPDATE}" ${TEST_JSON}

echo "^^^^^"

