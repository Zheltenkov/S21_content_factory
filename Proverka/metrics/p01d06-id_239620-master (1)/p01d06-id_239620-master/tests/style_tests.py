import subprocess
import os
import sys

PATH_TO_PROJECT = '/'.join(os.path.abspath(os.path.dirname(sys.argv[0])).split('/')[:-1])

test_name = sys.argv[1]
devnull_stderr = sys.stdout
devnull_stdout = sys.stdout


def get_source_filenames():
    arr_of_files = []
    if os.path.isdir(PATH_TO_PROJECT + '/src'):
      files = os.listdir(PATH_TO_PROJECT + '/src')
      for file in files:
          if file[file.find('.') + 1 : len(file)] == 'c':
              arr_of_files.append(file)
      return arr_of_files
    else:
      return arr_of_files


def copy_config():
    subprocess.run(['cp', PATH_TO_PROJECT + '/tests/linters/.clang-format', PATH_TO_PROJECT], stdout=devnull_stdout,
                   stderr=devnull_stderr)


def delete_config():
    subprocess.run(['rm', PATH_TO_PROJECT + '/.clang-format'], stdout=devnull_stdout, stderr=devnull_stderr)


def style_test_result(arr_of_files):
    copy_config()

    if arr_of_files == []:
        return False

    for i in range(len(arr_of_files)):
        if not os.path.exists(PATH_TO_PROJECT + '/src/' + arr_of_files[i]):
            return False
            
        result_style_test = subprocess.run(
            ['clang-format', '-n', PATH_TO_PROJECT + '/src/' + arr_of_files[i]],
            stderr=subprocess.STDOUT, stdout=subprocess.PIPE, text=True)
            
        if len(result_style_test.stdout) != 0:
            new_stdout = result_style_test.stdout.replace("^", "|").encode("utf-8", "ignore")
            print(new_stdout)
            delete_config()            
            return False

    delete_config()
    
    return True

arr_of_files = get_source_filenames()

if style_test_result(arr_of_files):
    print('Style test: OK 1')
else:
    print('Style test: FAIL 0')

