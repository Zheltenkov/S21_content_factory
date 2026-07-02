#include <stdio.h>
#include <math.h>

double important_function(double x);

#define EPS 1e-6


int main() {
    double x;

    if (scanf("%lf", &x) != 1 || fabs(x) < EPS) {
        printf("n/a");
        return -1;
    }

    printf("%.1lf", important_function(x));
    return 0;
}


double important_function(double x) {
    double y;
    y = 7e-3 * pow(x, 4) + ((22.8 * pow(x, 1.0 / 3.0) - 1e3) * x + 3) / (x * x / 2.0) - x * pow(10 + x, 2.0 / x) - 1.01;
    return y;
}
