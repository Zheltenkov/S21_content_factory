import subprocess
import os
import sys

PATH_TO_PROJECT = '/'.join(os.path.abspath(os.path.dirname(sys.argv[0])).split('/')[:-1])

test_name = sys.argv[1]

devnull_stderr = sys.stdout
devnull_stdout = sys.stdout


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
    

def build_program():
    try:
        build_folder = 'tests/src'
        arr_of_files = get_source_filenames()
        exe_filename = get_exe_filename()
        src_files_to_gcc = ''
        for i in range(len(arr_of_files)):
            src_files_to_gcc += PATH_TO_PROJECT + '/src/' + arr_of_files[i]
            if i != len(arr_of_files) - 1:
                src_files_to_gcc += ' '

        compile_exit_code = subprocess.run(['gcc', src_files_to_gcc, '-o', build_folder + '/' + exe_filename, '-lm'],
                                        stdout=devnull_stdout,
                                        stderr=devnull_stderr)
                                        
        return compile_exit_code.returncode
    except Exception as e:
        return -1
        
def run_test():
    result_build_code = build_program()
    if result_build_code != 0:
        print('\nBuild failed with exit code ' + str(result_build_code) + '. 0')
    else:
        print('\nProject build: OK 1')
    return

run_test()
