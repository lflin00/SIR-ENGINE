// MathUtils.java — demo file for SIR class scan + duplicate function scan

public class MathUtils {
    private int base;

    public MathUtils(int base) {
        this.base = base;
    }

    public int add(int x) {
        int result = this.base + x;
        return result;
    }

    public int multiply(int x) {
        int result = this.base * x;
        return result;
    }

    public boolean isPositive(int n) {
        if (n > 0) {
            return true;
        } else {
            return false;
        }
    }
}

// Standalone duplicate functions (same logic as MathUtils methods, different names)

public static int computeSum(int base, int x) {
    int result = base + x;
    return result;
}

public static int computeProduct(int base, int x) {
    int result = base * x;
    return result;
}
