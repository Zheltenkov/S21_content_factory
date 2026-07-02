import static java.lang.Math.sqrt;

public class MethodExamples {

    /**
     * Пример
     * <p>
     * Вычисление факториала
     */
    public static double factorial(int n) {
        double result = 1.0;
        for (int i = 1; i <= n; i++) {
            result *= i;
        }
        return result;
    }

    /**
     * Пример
     * <p>
     * Проверка числа на простоту -- результат true, если число простое
     */
    public static boolean isPrime(int n) {
        if (n < 2) return false;
        if (n == 2) return true;
        if (n % 2 == 0) return false;
        for (int m = 3; m <= (int) sqrt(n); m += 2) {
            if (n % m == 0) return false;
        }
        return true;
    }

    /**
     * Пример
     * <p>
     * Проверка числа на совершенность -- результат true, если число совершенное
     */
    public static boolean isPerfect(int n) {
        int sum = 1;
        for (int m = 2; m <= n / 2; m++) {
            if (n % m > 0) continue;
            sum += m;
            if (sum > n) break;
        }
        return sum == n;
    }

    /**
     * Пример
     * <p>
     * Найти число вхождений цифры m в число n
     */
    public static int digitCountInNumber(int n, int m) {
        return n == m ? 1 : (n < 10 ? 0 : digitCountInNumber(n / 10, m) + digitCountInNumber(n % 10, m));
    }
}
