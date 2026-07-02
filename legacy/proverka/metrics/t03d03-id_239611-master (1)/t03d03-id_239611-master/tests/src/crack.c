#include <stdio.h>
#include <math.h>

int in_circle(double x, double y);

int main() {
    double x, y;

    if (scanf("%lf%lf", &x, &y) != 2) {
        printf("n/a");
        return -1;
    }

    if (in_circle(x, y)) {
        printf("GOTCHA");
    } else {
        printf("MISS");
    }

    return 0;
}


int in_circle(double x, double y) {
    return (fabs(x) < 5 && fabs(y) < 5);
}