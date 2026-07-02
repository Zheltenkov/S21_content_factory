import subprocess
import os
import sys
import resource

PATH_TO_PROJECT = '/'.join(os.path.abspath(os.path.dirname(sys.argv[0])).split('/')[:-1])

test_name = sys.argv[1]
test_number = sys.argv[2]

test_timeout = 30
out_filename = f'{PATH_TO_PROJECT}/tests/test_out.txt'

devnull_stderr = subprocess.DEVNULL
devnull_stdout = subprocess.DEVNULL

arr_of_os = ['mac', 'ubuntu', 'alpine']

MAX_VIRTUAL_MEMORY = 100 * 1024 * 1024 # 100 MB - лимит по памяти

def get_test_file_in(os):
    try:
        test_file_in = open(PATH_TO_PROJECT + '/tests/' + test_name + '/' + os + '/' + test_number + '.in')
        test_file_in_text = test_file_in.read()
        test_file_in.close()
        return test_file_in_text
    except FileNotFoundError:
        print(
            '\nThe correct output file for comparing answer for test with number ' + test_number + ' does not exist.\n0')
        return None


def get_test_file_out(os):
    try:
        test_file_out = open(PATH_TO_PROJECT + '/tests/' + test_name + '/' + os + '/' + test_number + '.out')
        test_file_out_text = test_file_out.read()
        test_file_out.close()
        return test_file_out_text
    except FileNotFoundError:
        print(
            '\nThe correct output file for comparing answer for test with number ' + test_number + ' does not exist.\n0')
        return None


def get_args(os):
    try:
        test_file_out = open(PATH_TO_PROJECT + '/tests/' + test_name + '/' + os + '/' + test_number + '.args')
        test_file_out_text = test_file_out.read()
        test_file_out.close()
        return test_file_out_text
    except FileNotFoundError:
        print(
            '\nThe correct output file for comparing answer for test with number ' + test_number + ' does not exist.\n0')
        return None


def get_all_about_make():
    file = open(PATH_TO_PROJECT + '/tests/' + test_name + '/compile_file')
    all_about_make = file.read().split()
    file.close()
    return all_about_make


def get_exe_filename():
    makefile_dir, makefile_name, makefile_stage = get_all_about_make()
    return PATH_TO_PROJECT + '/' + makefile_dir + '/' + test_name


def functional_program_test(program_name, file_in_text, file_out_text, args, os):
    with open(out_filename, 'w+') as file_out:
        subprocess.run(f'{program_name} {args}',
                       stdout=file_out, input=file_in_text,
                       stderr=devnull_stderr, text=True,
                       cwd=PATH_TO_PROJECT + '/tests/' + test_name + '/' + os + '/',
                       timeout=test_timeout, shell=True)

        file_out.seek(0, 0)
        execution_result = file_out.read()

        return file_out_text == execution_result


def memory_program_test(program_name, file_in_text, args, os):
    with open(out_filename, 'w+') as file_out:
        valgrind_test_result = subprocess.run(f'valgrind --tool=memcheck --leak-check=yes {program_name} {args}',
                                              input=file_in_text, timeout=test_timeout, stdout=devnull_stdout,
                                              stderr=file_out,
                                              text=True, shell=True,
                                              cwd=PATH_TO_PROJECT + '/tests/' + test_name + '/' + os + '/')

        file_out.seek(0, 0)
        errors = file_out.read()
        right_test_str = 'ERROR SUMMARY: 0 errors from 0 contexts (suppressed: 0 from 0)'

        errors_lines = errors.split('\n')
        for i in range(len(errors_lines)):
            if 'Command:' in errors_lines[i]:
                errors_lines.pop(i)
                break
        errors = '\n'.join(errors_lines)

        print('Memory test output:\n' + errors)

        return right_test_str in errors


def run_one_os_test(os):
    file_in_text = get_test_file_in(os)
    if file_in_text is None:
        return
    file_out_text = get_test_file_out(os)
    if file_out_text is None:
        return
    args = get_args(os)
    if args is None:
        return

    functional_program_test_result = functional_program_test(get_exe_filename(),
                                                             file_in_text, file_out_text, args, os)

    return functional_program_test_result, file_in_text, args


def get_results_of_testing():
    global arr_of_os
    functional_program_test_result = False
    memory_program_test_result = False
    i = 0
    while not functional_program_test_result and i < len(arr_of_os):
        try:
            functional_program_test_result, file_in_text, args = run_one_os_test(arr_of_os[i])
        except subprocess.TimeoutExpired:
            return None, None
        i += 1

    print('Functional test output: ' + str(functional_program_test_result))

    try:
        memory_program_test_result = memory_program_test(get_exe_filename(), file_in_text, args, arr_of_os[i - 1])
    except subprocess.TimeoutExpired:
        return None, None

    return functional_program_test_result, memory_program_test_result

# установка лимита по памяти
def limit_virtual_memory():
    # The tuple below is of the form (soft limit, hard limit). Limit only
    # the soft part so that the limit can be increased later (setting also
    # the hard limit would prevent that).
    # When the limit cannot be changed, setrlimit() raises ValueError.
    resource.setrlimit(resource.RLIMIT_AS, (MAX_VIRTUAL_MEMORY, resource.RLIM_INFINITY))
    

def run_test():
    limit_virtual_memory()
    try:
        functional_program_test_result, memory_program_test_result = get_results_of_testing()
    except UnicodeDecodeError:
        functional_program_test_result = False
        memory_program_test_result = False
    except MemoryError:
        functional_program_test_result = False
        memory_program_test_result = False

    is_func_ok = False
    if functional_program_test_result:
        print('Result for test with number ' + str(test_number) + ': OK\n')
        is_func_ok = True
    elif functional_program_test_result is None:
        print('Result for test with number ' + str(test_number) + ': TIMEOUT ERROR\n')
    else:
        print('Result for test with number ' + str(test_number) + ': FAIL\n')

    is_memory_ok = False
    if memory_program_test_result:
        print('Memory test: OK ')
        is_memory_ok = True
    elif memory_program_test_result is None:
        print('Memory test: TIMEOUT ERROR ')
    else:
        print('Memory test: FAIL ')

    if is_func_ok and is_memory_ok:
        print('1')
    elif is_func_ok and not is_memory_ok:
        print('-1')
    else:
        print('0')


run_test()
