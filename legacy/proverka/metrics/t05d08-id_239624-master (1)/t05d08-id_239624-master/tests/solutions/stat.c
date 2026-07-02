#include <stdio.h>
#define NMAX 10

int input(int *a, int *n);
void output(int *a, int n);
int max(int *a, int n);
int min(int *a, int n);
double mean(int *a, int n);
double variance(int *a, int n);

void output_result(int max_v, int min_v, double mean_v, double variance_v);

int input(int *a, int *n) {
    if (scanf("%d", n) != 1) return 1;  // если н не получилось считать :(
    if (*n < 0 || *n > NMAX) return 1;  // если ушел за пределы
    for (int i = 0; i < *n; i++) {
        if (scanf("%d", &a[i]) != 1) return 1;  // если не удалось прочитать эелемент массива
    }
    return 0;
}

int max(int *a, int n) {
    if (n == 0) return 0;  // проверка
    int maximum = a[0];
    for (int i = 0; i < n; i++) {
        if (a[i] > maximum) maximum = a[i];
    }
    return maximum;
}

int min(int *a, int n) {
    if (n == 0) return 0;  // проверка
    int minimum = a[0];
    for (int i = 1; i < n; i++) {
        if (a[i] < minimum) minimum = a[i];
    }
    return minimum;
}

double mean(int *a, int n) {
    if (n == 0) return 0.0;  // ну на всякийййй случай если массив пустой
    double sum = 0;
    for (int i = 0; i < n; i++) {
        sum += a[i];
    }
    return sum / n;
}

double variance(int *a, int n) {
    if (n == 0) return 0.0;
    double m = mean(a, n);
    double sum_sq = 0;
    for (int i = 0; i < n; i++) {
        sum_sq += (a[i] - m) * (a[i] - m);  // сумма квадратов отклонений :с
    }
    return sum_sq / n;
}

void output(int *a, int n) {
    for (int i = 0; i < n; i++) {
        if (i > 0) printf(" ");  // разделяем при выводе пробелом
        printf("%d", a[i]);
    }
    printf("\n");
}

void output_result(int max_v, int min_v, double mean_v, double variance_v) {
    printf("%d %d %.6f %.6f\n", max_v, min_v, mean_v,
           variance_v);  // тута определил точность в 6 знаков с помощью %.6f
}

int main() {
    int n, data[NMAX];
    if (input(data, &n) != 0) {
        printf("n/a\n");
        return 1;
    }
    output(data, n);
    output_result(max(data, n), min(data, n), mean(data, n), variance(data, n));

    return 0;
}
