#include <math.h>  // штобы sqrt работал
#include <stdio.h>
#define NMAX 30

int input(int *a, int *n);  // Читает n и массив
double mean(int *a, int n);
double variance(int *a, int n);
int search(int *a, int n, double mean_v, double variance_v);

int main() {
    int n, data[NMAX];
    if (input(data, &n) != 0) {  // Проверяем ввод
        printf("n/a\n");
        return 1;
    }

    if (n == 0) {  // Если массив пустой, ничего не найдено
        printf("0\n");
        return 0;
    }

    double m = mean(data, n);              // Вычисляем среднее
    double var = variance(data, n);        // Вычисляем дисперсию
    int result = search(data, n, m, var);  // Ищем подходящее число
    printf("%d\n", result);                // Выводим результат

    return 0;
}

int input(int *a, int *n) {
    if (scanf("%d", n) != 1) return 1;  // если не удалось прочитать n
    if (*n < 0 || *n > NMAX) return 1;  // если n вне диапазона
    for (int i = 0; i < *n; i++) {
        if (scanf("%d", &a[i]) != 1) return 1;  // если не удалось прочитать элемент
    }
    return 0;
}

// среднее
double mean(int *a, int n) {
    if (n == 0) return 0.0;
    double sum = 0;
    for (int i = 0; i < n; i++) {
        sum += a[i];
    }
    return sum / n;
}

// дисперсия
double variance(int *a, int n) {
    if (n == 0) return 0.0;
    double m = mean(a, n);
    double sum_sq = 0;
    for (int i = 0; i < n; i++) {
        sum_sq += (a[i] - m) * (a[i] - m);
    }
    return sum_sq / n;
}

// поиск нужного числа
int search(int *a, int n, double mean_v, double variance_v) {
    double std_dev = sqrt(variance_v);          // отклонение
    double upper_limit = mean_v + 3 * std_dev;  // верхняя граница правила трёх сигм

    for (int i = 0; i < n; i++) {
        int x = a[i];
        if (x % 2 == 0 &&        // чётное
            x >= mean_v &&       // >= mean
            x <= upper_limit &&  // <= mean + 3 * sqrt(variance)
            x != 0) {
            return x;  // вернем первое подходящее число
        }
    }
    return 0;  // Ничего не найдено
}

/*
    Search module for the desired value from data array.

    Returned value must be:
        - "even"
        - ">= mean"
        - "<= mean + 3 * sqrt(variance)"
        - "!= 0"

        OR

        0
*/
