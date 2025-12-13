Gamma Structure
    Gamma provides some organizational models. These include events, and markets. The most fundamental element is always markets and the other models simply provide additional organization.
​
Detail
    Market
        Contains data related to a market that is traded on. Maps onto a pair of clob token ids, a market address, a question id and a condition id
    Event:  
        Contains a set of markets
    Variants:
        Event with 1 market (i.e., resulting in an SMP)
        Event with 2 or more markets (i.e., resulting in an GMP)
    ​
Example
    [Event] Where will Barron Trump attend College?
        [Market] Will Barron attend Georgetown?
        [Market] Will Barron attend NYU?
        [Market] Will Barron attend UPenn?
        [Market] Will Barron attend Harvard?
        [Market] Will Barron attend another college?