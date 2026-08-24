package main

import (
    "fmt"
)

// מבנה בסיסי עבור חיה
type Animal struct {
    Name string
    Age  int
}

// מתודה של Animal
func (a Animal) Breathe() {
    fmt.Printf("%s נושם\n", a.Name)
}

// מבנה Dog המשתמש בקומפוזיציה עם Animal
type Dog struct {
    Animal
    Breed string
}

// מתודה ייחודית ל-Dog
func (d Dog) Bark() {
    fmt.Printf("%s נובח: הב הב!\n", d.Name)
}

func main() {
    // יצירת מופע של Dog
    myDog := Dog{
        Animal: Animal{Name: "רקס", Age: 5},
        Breed:  "לברדור",
    }

    // שימוש בשדות ומתודות שנירשו מ-Animal
    fmt.Printf("%s הוא %d שנים\n", myDog.Name, myDog.Age)
    myDog.Breathe()

    // שימוש במתודה ייחודית של Dog
    myDog.Bark()

    // גישה לשדה ייחודי של Dog
    fmt.Printf("%s הוא מגזע %s\n", myDog.Name, myDog.Breed)
}
