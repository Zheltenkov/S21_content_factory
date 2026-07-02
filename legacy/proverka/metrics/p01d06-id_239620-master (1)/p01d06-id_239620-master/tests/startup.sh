#!/bin/bash

# Input parameters
test_name=$1
test_number=$2

# Path to folders
script_dir="$(dirname ${BASH_SOURCE[0]})"
project_dir="${script_dir}/.."
right_tests=0

exec 2>/dev/null # So that the results of the linters are not output to the console

# A function for testing the source code style. $1 - file name. Returns: 0 - success, 1 - there are errors in the styles
function style_test {
	cp ${project_dir}/tests/CPPLINT.cfg ${project_dir}
	path_to_cpplint="${project_dir}/tests/linters/cpplint.py"
	result=`python3 ${path_to_cpplint} --extensions=c --quiet ${project_dir}/src/*.c`
	# The --quiet flag when called cpplint.py means that if there are no errors, nothing will be output
	result_len=`expr length "$result"`
	rm ${project_dir}/CPPLINT.cfg
	if [ $result_len -eq 0 ]
	then
		return 0
	else
		return 1
	fi
}

# A function for static analysis of source code using cpp. $1 - file name. Returns: 0 - success, 1 - there are errors in the code
function cppcheck_test {
	test_tmpfile=$(mktemp)
	cppcheck -q --enable=all --suppress=missingIncludeSystem ${project_dir}/src/*.c 2> $test_tmpfile
	result=`cat $test_tmpfile`
	result_len=`expr length "$result"`
	rm $test_tmpfile 2> /dev/null

	if [ $result_len -eq 0 ]
	then
		return 0
	else
		return 1
	fi
}

# style and static code analysis
style_test
if [ $? -eq 1 ]
  then
    echo -e "\nStyle test: FAIL\n"
  else
    echo -e "\nStyle test: OK\n"
    let "right_tests += 1"
fi

cppcheck_test
if [ $? -eq 1 ]
  then
    echo -e "Static code analysis: FAIL\n"
  else
    echo -e "Static code analysis: OK\n"
    let "right_tests += 1"
fi


if [ $right_tests -eq 2 ]
then
	echo "1"
else
	echo "0"
fi

exit 0

