// ArithmeticHelper.java — intentional structural duplicate of MathUtils

public class ArithmeticHelper {
    private int offset;

    public ArithmeticHelper(int offset) {
        this.offset = offset;
    }

    public int add(int n) {
        int total = this.offset + n;
        return total;
    }

    public int multiply(int n) {
        int total = this.offset * n;
        return total;
    }

    public boolean isPositive(int value) {
        if (value > 0) {
            return true;
        } else {
            return false;
        }
    }
}

// Duplicate of computeSum / computeProduct with different names
public static int addValues(int a, int b) {
    int result = a + b;
    return result;
}

public static int multiplyValues(int a, int b) {
    int result = a * b;
    return result;
}
