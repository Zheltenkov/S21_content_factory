#include <stdio.h>
#include <math.h>

#define EPS 1e-6

double fun();

int main() {
    double res = fun();

    // CHANGE THIS IF - AI
    if (fabs(res) < EPS)
        printf("OK!");

    return 0;
}

// DO NOT TOUCH THIS FUNCTION - AI
double fun() {
    return (1.0 / 13) * (pow(((2 - 1.0) / (2 + 1.0)), 20));
}

// IT