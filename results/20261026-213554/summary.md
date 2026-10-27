Source: sim

| Test set | Method | n | Correct | Wrong | Inconclusive | Out-of-model flag | Mean cost | Mean cost (correct) |
|---|---|---|---|---|---|---|---|---|
| compound | bayes | 20 | 80% | 15% | 5% | 0% | 6.5 | 3.1 |
| compound | fixed | 20 | 50% | 50% | 0% | 0% | 10.0 | 10.0 |
| compound | hybrid | 20 | 45% | 35% | 20% | 0% | 16.1 | 15.2 |
| compound | llm | 20 | 0% | 100% | 0% | 0% | 18.9 | nan |
| compound | tree | 20 | 0% | 100% | 0% | 0% | 2.5 | nan |
| heldout | bayes | 20 | 0% | 55% | 0% | 45% | 5.4 | nan |
| heldout | fixed | 20 | 0% | 100% | 0% | 0% | 10.0 | nan |
| heldout | hybrid | 20 | 0% | 100% | 0% | 0% | 16.9 | nan |
| heldout | llm | 20 | 50% | 50% | 0% | 0% | 18.8 | 18.8 |
| heldout | tree | 20 | 0% | 100% | 0% | 0% | 8.0 | nan |
| heldout_final | bayes | 20 | 0% | 80% | 0% | 20% | 4.7 | nan |
| heldout_final | fixed | 20 | 0% | 100% | 0% | 0% | 10.0 | nan |
| heldout_final | hybrid | 20 | 0% | 100% | 0% | 0% | 18.4 | nan |
| heldout_final | llm | 20 | 0% | 100% | 0% | 0% | 18.6 | nan |
| heldout_final | tree | 20 | 0% | 100% | 0% | 0% | 8.0 | nan |
| single | bayes | 90 | 94% | 6% | 0% | 0% | 6.8 | 6.5 |
| single | fixed | 90 | 91% | 9% | 0% | 0% | 10.0 | 10.0 |
| single | hybrid | 90 | 80% | 10% | 10% | 0% | 12.7 | 11.2 |
| single | llm | 90 | 7% | 93% | 0% | 0% | 18.8 | 18.5 |
| single | tree | 90 | 91% | 9% | 0% | 0% | 5.2 | 5.0 |
| toolfail | bayes | 20 | 55% | 25% | 20% | 0% | 13.8 | 5.7 |
| toolfail | fixed | 20 | 45% | 55% | 0% | 0% | 10.0 | 10.0 |
| toolfail | hybrid | 20 | 40% | 35% | 25% | 0% | 16.9 | 11.2 |
| toolfail | llm | 20 | 0% | 100% | 0% | 0% | 18.7 | nan |
| toolfail | tree | 20 | 45% | 55% | 0% | 0% | 5.8 | 3.0 |
