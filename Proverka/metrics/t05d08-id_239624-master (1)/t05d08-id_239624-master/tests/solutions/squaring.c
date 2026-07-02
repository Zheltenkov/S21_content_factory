#include <stdio.h>
#define NMAX 10

int input(int *a, int *n);
void output(int *a, int n);
void squaring(int *a, int n);

int main() {
    int n, data[NMAX];
    if (input(data, &n) != 0) {
        printf("n/a");
        return 1;
    }
    squaring(data, n);
    output(data, n);

    return 0;
}

int input(int *a, int *n) {
    if (scanf("%d", n) != 1) return 1;  // если не читается n то вернем ошибку
    if (*n < 0 || *n > NMAX) return 1;  // тута если n не вписывается в диапозон от 0 до NMAX
    for (int *p = a; p - a < *n; p++) {
        if (scanf("%d", p) != 1) return 1;  // еще одна проверка на всякий случай
    }
    return 0;  // функция должна возвращать что-та
}

void output(int *a, int n) {
    for (int i = 0; i < n; i++) {
        if (i > 0) printf(" ");  // Разделитель между элементами
        printf("%d", a[i]);
    }
    printf("\n");  //
    // NOTHING
}

void squaring(int *a, int n) {
    for (int i = 0; i < n; i++) {
        a[i] = a[i] * a[i];  // в квадрат
    }  // NOTHING
}
