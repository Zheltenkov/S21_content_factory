#!/bin/bash

#
# Скрипт для формирования итогового json по результатам теста
# Берет TEST_JSON, проходит по полям number и запускает скрипт STARTUP_SCRIPT с аргументами number и name
# В ответ получает результат теста
# В конце делает подмену поля result на значение, полученное от STARTUP_SCRIPT
#

echo -e "Run tests:\n"
echo -e "\n21 School\n"
echo -e "\nVERTER is watching your code...¯\_(*_*)_/¯\n"
set -e

# Запуск стилевых тестов
echo -e "-------------------------------------------------------------------------------\n"
echo -e "Style test\n"
STYLE_TEST_SCRIPT=tests/style_tests.sh
OUTPUT=$(bash ${STYLE_TEST_SCRIPT})
printf 'Style test output:\n %s\n' "$OUTPUT"
STYLE_TEST_RESULT=${OUTPUT: -1}
printf 'Style test result: %s\n' "$STYLE_TEST_RESULT"
echo -e "-------------------------------------------------------------------------------\n"

# Шаблон
TEST_JSON=tests/tests.json

# Скрипт запуска тестов
STARTUP_SCRIPT=tests/startup.sh

for TEST_NUMBER in $(jq ".tests[].number" ${TEST_JSON}); do

  # Получаем имя теста и категорию теста
  NAME_CATEGORY=$(jq -r ".tests[$TEST_NUMBER].name" ${TEST_JSON})
  # Разбиваем строку
  IFS='.' read -ra ARR_NAME_CATEGORY <<< "$NAME_CATEGORY"
  # Получаем имя теста
  NAME=${ARR_NAME_CATEGORY[0]}
  # Получаем категорию теста
  CATEGORY=${ARR_NAME_CATEGORY[1]}
  PART=${CATEGORY: -1}

  if [ "$LAST_PART" != "$PART" ]
  then
    echo -e "-------------------------------------------------------------------------------\n"
    echo -e "Part:" ${PART}"\n"
    LAST_PART=$PART
    # Билд библиотеки и юнит-тестов
    BUILD_SCRIPT=ci-scripts/build.sh
    OUTPUT=$(bash ${BUILD_SCRIPT} ${NAME})
    printf 'Build output:\n%s\n' "$OUTPUT"
    BUILD_RESULT=${OUTPUT: -1}
    printf 'Build result: %s\n' "$BUILD_RESULT"
    MEMORY_TEST_RESULT=1
  fi

  echo -e "-------------------------------------------------------------------------------\n"
  echo -e Test number: ${TEST_NUMBER}, name: ${NAME}"\n"
  if [ $BUILD_RESULT -eq 1 ]
  then
    # Получаем результат теста
    OUTPUT=$(bash ${STARTUP_SCRIPT} ${NAME} ${TEST_NUMBER})
    LAST_LINE=$(echo "$OUTPUT" | tail -n1)
    if [ $STYLE_TEST_RESULT -eq 1 ] && [ $MEMORY_TEST_RESULT -eq 1 ]
    then
      RESULT=$LAST_LINE
    else
      RESULT=0
    fi

    if [[ $RESULT == -* ]]
    then
      MEMORY_TEST_RESULT=0
      RESULT=0
	  printf 'Bad memory test result - failing ALL others results of this task part!\n\n'

      # Bad design - remake later when new pipeline launched!!!
      # If there was memory test result error - zeroes all previous tests
      for PASSED_TEST_NUMBER in $(jq ".tests[].number" ${TEST_JSON}); do
        if [ $PASSED_TEST_NUMBER -ge $TEST_NUMBER ]
        then
          break
        else 
          UPDATE=${UPDATE}"| .tests[${PASSED_TEST_NUMBER}].result = 0 "
        fi
      done
    fi

    printf 'Test output:\n%s\n' "$OUTPUT"
    printf 'Test result: %s\n' "$RESULT"
  else
    echo -e Test output: Build fail"\n"
    echo -e Test result: 0"\n"
    RESULT=0
  fi

  # Формируем строку для подмены результатов в шаблоне
  UPDATE=${UPDATE}"| .tests[${TEST_NUMBER}].result = ${RESULT} "
done

echo -e "-------------------------------------------------------------------------------\n"

echo -e "\n"

echo "vvvvv"

# Формируем результат
jq ". ${UPDATE}" ${TEST_JSON}

echo "^^^^^"
