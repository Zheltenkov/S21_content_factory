package testNGTests;

public class TestsExampleTestNG {

    public void is1shouldBeThird() {
        //Метод должен быть вызван третьим
        System.out.println("Hi I am third test");
        System.out.println("Here is my assertion");
        //assertTrue(145 > 0);
        System.out.println("Now I will perish");
        System.out.println("Bye");
    }

    public void is2shouldBeFirst() {
        //Метод должен быть вызван первым
        System.out.println("Hi I am first test");
        System.out.println("Here is my assertion");
        //assertTrue(6 > 5);
        System.out.println("Now I will perish");
        System.out.println("Bye");
    }

    public void is3shouldBeSecond() {
        //Метод должен быть вызван вторым
        System.out.println("Hi I am second test");
        System.out.println("Here is my assertion");
        //assertTrue(true);
        System.out.println("Now I will perish");
        System.out.println("Bye");
    }
}
