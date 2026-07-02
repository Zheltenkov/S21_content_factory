import subprocess
import os
import sys

PATH_TO_PROJECT = '/'.join(os.path.abspath(os.path.dirname(sys.argv[0])).split('/')[:-1])
SRC_PATH = PATH_TO_PROJECT + '/src/'

test_name = sys.argv[1]
test_number = sys.argv[2]

test_timeout = 5
out_filename = f'{PATH_TO_PROJECT}/tests/test_out.txt'

devnull_stderr = subprocess.DEVNULL
devnull_stdout = subprocess.DEVNULL

def get_test_file_in():
    try:
        test_file_in = open(PATH_TO_PROJECT + '/tests/' + test_name + '/' + test_number + '.in')
        test_file_in_text = test_file_in.read()
        test_file_in.close()
        return test_file_in_text
    except FileNotFoundError:
        print(
            '\nThe correct output file for comparing answer for test with number ' + test_number + ' does not exist.\n 0')
        return None


def get_test_file_out():
    try:
        test_file_out = open(PATH_TO_PROJECT + '/tests/' + test_name + '/' + test_number + '.out')
        test_file_out_text = test_file_out.read()
        test_file_out.close()
        return test_file_out_text
    except FileNotFoundError:
        print(
            '\nThe correct output file for comparing answer for test with number ' + test_number + ' does not exist.\n 0')
        return None
        
        
def get_args():
    try:
        test_file_out = open(PATH_TO_PROJECT + '/tests/' + test_name + '/' + test_number + '.args')
        test_file_out_text = test_file_out.read()
        test_file_out.close()
        return test_file_out_text
    except FileNotFoundError:
        print(
            '\nThe correct output file for comparing answer for test with number ' + test_number + ' does not exist.\n 0')
        return None


def get_source_filenames():
    file = open(PATH_TO_PROJECT + '/tests/' + test_name + '/compile_file')
    arr_of_files = file.read().split()[:-1]
    file.close()
    return arr_of_files


def get_exe_filename():
    file = open(PATH_TO_PROJECT + '/tests/' + test_name + '/compile_file')
    exe_filename = file.read().split()[-1]
    file.close()
    return exe_filename


def functional_program_test(program_name, file_in_text, file_out_text, args):
    with open(out_filename, 'w+') as file_out:
        execution_result = subprocess.run(f'{program_name} {args}', text=True, stdout=file_out, input=file_in_text,
                                          stderr=devnull_stderr, timeout=test_timeout, shell=True)
        file_out.seek(0)
        execution_result = file_out.read()
        if len(execution_result) > 0 and execution_result[-1] == '\n':
            execution_result = execution_result[:-1]

        return file_out_text == execution_result


def run_test():
    if not os.path.exists(SRC_PATH) or len(os.listdir(SRC_PATH)) == 0:
        print('\nEmpty repository!\n 0')
        return

    file_in_text = get_test_file_in()
    if file_in_text is None:
        return
    file_out_text = get_test_file_out()
    if file_out_text is None:
        return
    args = get_args()
    if args is None:
        return

    try:
        functional_program_test_result = functional_program_test(PATH_TO_PROJECT + '/tests/src/' + get_exe_filename(),
                                                                file_in_text, file_out_text, args)
    except subprocess.TimeoutExpired:
        print('\nResult for test with number ' + str(test_number) + ': TIMEOUT ERROR 0')
        return

    count_right_tests = 0
    if functional_program_test_result:
        print('Result for test with number ' + str(test_number) + ': OK', end=' ')
        count_right_tests += 1
    else:
        print('Result for test with number ' + str(test_number) + ': FAIL', end=' ')
        
    if count_right_tests == 1:
        print('1')
    else:
        print('0')


run_test()
