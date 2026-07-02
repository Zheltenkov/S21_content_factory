#include <stdio.h>

#define NAME_LENGTH 100


int main() {
    char name[NAME_LENGTH];

    scanf("%s", name);

    printf("Hello, %s!", name);
    return 0;
}
